import json
import os
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.mcp.repository_resolver import RepositoryResolver
from app.models.code_chunk import CodeChunk
from app.models.repository import Repository
from app.rag.chunking import WorkspaceChunker
from app.rag.embeddings import (
    EmbeddingProvider,
    create_embedding_provider,
)
from app.rag.models import CodeChunkDraft, IndexSummary


_INDEXABLE_EXTENSIONS = {
    ".go",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_IGNORED_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


class RepositoryIndexService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        repository_resolver: RepositoryResolver | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        chunker: WorkspaceChunker | None = None,
        batch_size: int | None = None,
    ):
        self.session_factory = session_factory
        self.repository_resolver = repository_resolver or RepositoryResolver(
            session_factory
        )
        self.embedding_provider = (
            embedding_provider or create_embedding_provider()
        )
        self.chunker = chunker or WorkspaceChunker()
        self.batch_size = batch_size or settings.rag_chunk_batch_size
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")

    async def index_repository(
        self,
        repository_id: int,
    ) -> IndexSummary:
        workspace = self._resolve_workspace(repository_id)
        file_paths = self._list_files(workspace)
        return await self._update(
            repository_id=repository_id,
            workspace=workspace,
            file_paths=file_paths,
            full_index=True,
        )

    async def update_files(
        self,
        repository_id: int,
        file_paths: list[str],
    ) -> IndexSummary:
        workspace = self._resolve_workspace(repository_id)
        normalized = list(
            dict.fromkeys(
                path.replace("\\", "/").lstrip("/")
                for path in file_paths
                if path
            )
        )
        return await self._update(
            repository_id=repository_id,
            workspace=workspace,
            file_paths=normalized,
            full_index=False,
        )

    def delete_file_chunks(
        self,
        repository_id: int,
        file_path: str,
    ) -> int:
        normalized = file_path.replace("\\", "/").lstrip("/")
        db = self.session_factory()
        try:
            result = db.execute(
                delete(CodeChunk).where(
                    CodeChunk.repository_id == repository_id,
                    CodeChunk.file_path == normalized,
                )
            )
            db.commit()
            return int(result.rowcount or 0)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _resolve_workspace(self, repository_id: int) -> Path:
        db = self.session_factory()
        try:
            repository = db.get(Repository, repository_id)
            if repository is None:
                raise ValueError("Repository not found")
            user_id = repository.user_id
        finally:
            db.close()
        resolved = self.repository_resolver.resolve_owned_workspace(
            repository_id,
            user_id,
        )
        return Path(resolved.local_path).resolve()

    def _list_files(self, workspace: Path) -> list[str]:
        files: list[str] = []
        for root, directories, names in os.walk(workspace):
            directories[:] = [
                name
                for name in directories
                if name.lower() not in _IGNORED_DIRECTORIES
            ]
            root_path = Path(root)
            for name in names:
                path = root_path / name
                if path.suffix.lower() not in _INDEXABLE_EXTENSIONS:
                    continue
                files.append(path.relative_to(workspace).as_posix())
        return sorted(files)

    async def _update(
        self,
        *,
        repository_id: int,
        workspace: Path,
        file_paths: list[str],
        full_index: bool,
    ) -> IndexSummary:
        drafts_by_file: dict[str, list[CodeChunkDraft]] = {}
        seen_files: set[str] = set()
        for file_path in file_paths:
            normalized, absolute = self._safe_path(workspace, file_path)
            if not absolute.is_file():
                drafts_by_file[normalized] = []
                continue
            try:
                content = absolute.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                drafts_by_file[normalized] = []
                continue
            seen_files.add(normalized)
            drafts_by_file[normalized] = self.chunker.chunk_file(
                normalized,
                content,
            )

        db = self.session_factory()
        try:
            existing = list(
                db.scalars(
                    select(CodeChunk).where(
                        CodeChunk.repository_id == repository_id
                    )
                )
            )
        finally:
            db.close()
        existing_by_file: dict[str, list[CodeChunk]] = {}
        for chunk in existing:
            existing_by_file.setdefault(chunk.file_path, []).append(chunk)

        if full_index:
            deleted_files = set(existing_by_file) - seen_files
            for file_path in deleted_files:
                drafts_by_file.setdefault(file_path, [])
        else:
            deleted_files = {
                file_path
                for file_path, drafts in drafts_by_file.items()
                if not drafts and file_path in existing_by_file
            }

        replace_files: set[str] = set()
        unchanged_files = 0
        for file_path, drafts in drafts_by_file.items():
            draft_keys = {
                (
                    draft.content_hash,
                    draft.start_line,
                    draft.end_line,
                )
                for draft in drafts
            }
            existing_keys = {
                (
                    chunk.content_hash,
                    chunk.start_line,
                    chunk.end_line,
                )
                for chunk in existing_by_file.get(file_path, [])
            }
            if draft_keys == existing_keys:
                unchanged_files += 1
            else:
                replace_files.add(file_path)

        drafts_to_write = [
            draft
            for file_path in sorted(replace_files)
            for draft in drafts_by_file[file_path]
        ]
        embeddings: list[list[float]] = []
        for offset in range(0, len(drafts_to_write), self.batch_size):
            batch = drafts_to_write[offset : offset + self.batch_size]
            batch_embeddings = (
                await self.embedding_provider.embed_documents(
                    [draft.content for draft in batch]
                )
            )
            if len(batch_embeddings) != len(batch):
                raise RuntimeError("Embedding response count mismatch")
            embeddings.extend(batch_embeddings)

        chunks_deleted = sum(
            len(existing_by_file.get(file_path, []))
            for file_path in replace_files
        )
        db = self.session_factory()
        try:
            if replace_files:
                db.execute(
                    delete(CodeChunk).where(
                        CodeChunk.repository_id == repository_id,
                        CodeChunk.file_path.in_(replace_files),
                    )
                )
            for draft, embedding in zip(
                drafts_to_write,
                embeddings,
                strict=True,
            ):
                db.add(
                    CodeChunk(
                        repository_id=repository_id,
                        file_path=draft.file_path,
                        language=draft.language,
                        symbol_name=draft.symbol_name,
                        chunk_type=draft.chunk_type,
                        start_line=draft.start_line,
                        end_line=draft.end_line,
                        content=draft.content,
                        content_hash=draft.content_hash,
                        embedding_json=json.dumps(
                            embedding,
                            separators=(",", ":"),
                        ),
                    )
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        indexed_files = sum(
            bool(drafts_by_file[file_path])
            for file_path in replace_files
        )
        return IndexSummary(
            repository_id=repository_id,
            files_indexed=indexed_files,
            files_unchanged=unchanged_files,
            files_deleted=len(deleted_files),
            chunks_written=len(drafts_to_write),
            chunks_deleted=chunks_deleted,
        )

    @staticmethod
    def _safe_path(
        workspace: Path,
        file_path: str,
    ) -> tuple[str, Path]:
        normalized = file_path.replace("\\", "/").lstrip("/")
        absolute = (workspace / normalized).resolve()
        try:
            relative = absolute.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("File path escapes repository workspace") from exc
        return relative.as_posix(), absolute
