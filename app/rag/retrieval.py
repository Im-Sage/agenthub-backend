import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.core.logging import get_logger, log_agent_event
from app.mcp.repository_resolver import RepositoryResolver
from app.models.code_chunk import CodeChunk
from app.rag.embeddings import (
    EmbeddingProvider,
    create_embedding_provider,
)
from app.services.workspace_service import workspace_service


logger = get_logger("rag.search")


class RetrievedCodeChunk(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    content: str
    keyword_rank: int | None
    vector_rank: int | None
    combined_score: float


@dataclass
class _Candidate:
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    content: str

    @property
    def identity(self) -> tuple[str, int, int]:
        return (self.file_path, self.start_line, self.end_line)


def cosine_similarity(
    left: list[float],
    right: list[float],
) -> float:
    if not left or not right:
        return 0.0
    if len(left) != len(right):
        raise ValueError("Embedding dimension mismatch")
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    denominator = float(
        np.linalg.norm(left_array) * np.linalg.norm(right_array)
    )
    if denominator == 0.0:
        return 0.0
    return float(np.dot(left_array, right_array) / denominator)


class HybridCodeRetriever:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        repository_resolver: RepositoryResolver | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        workspace_search=workspace_service,
    ):
        self.session_factory = session_factory
        self.repository_resolver = repository_resolver or RepositoryResolver(
            session_factory
        )
        self.embedding_provider = (
            embedding_provider or create_embedding_provider()
        )
        self.workspace_search = workspace_search

    # search的主要功能是根据用户提供的查询字符串，在指定的仓库中检索相关的代码片段。
    # 它结合了关键字搜索和向量搜索的方法，以提高检索的准确性和相关性。以下是search方法的详细说明：
    # 1. 参数：
    # - repository_id: 用户要搜索的仓库的唯一标识符。
    # - user_id: 发起搜索请求的用户的唯一标识符。
    # - query: 用户输入的搜索查询字符串。
    # - top_k: 可选参数，指定返回的最相关代码片段的数量，默认为8。
    # 2. 功能：
    # - 记录搜索开始时间，以便计算搜索耗时。
    # - 对查询字符串进行规范化处理，去除首尾空格，并检查是否为空。
    # - 验证top_k参数的有效性，确保其为1到20之间的整数。
    # - 使用repository_resolver验证用户对指定仓库的访问权限，并获取仓库的本地路径。
    # - 调用_keyword_candidates方法进行关键字搜索，获取与查询字符串匹配的代码片段候选列表。
    # - 调用_vector_candidates方法进行向量搜索，获取与查询字符串语义相关的代码片候选列表。
    # - 调用_fuse方法将关键字搜索和向量搜索的结果进行融合，生成一个综合评分的代码片段列表。
    # - 调用_apply_budgets方法根据top_k参数限制返回的代码片段数量，并确保内容长度不超过指定的限制。
    # - 记录搜索完成事件，包括搜索耗时、检索到的代码片段数量、关键字候选数量和向量候选数量。
    # - 返回最终的检索结果列表。
    async def search(
        self,
        *,
        repository_id: int,
        user_id: int,
        query: str,
        top_k: int = 8,
    ) -> list[RetrievedCodeChunk]:
        started = time.perf_counter()
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise ValueError("top_k must be an integer from 1 to 20")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")

        resolved = self.repository_resolver.resolve_owned_workspace(
            repository_id,
            user_id,
        )
        keyword_candidates = self._keyword_candidates(
            resolved.local_path,
            normalized_query,
        )
        vector_candidates = await self._vector_candidates(
            repository_id,
            normalized_query,
        )
        fused = self._fuse(
            keyword_candidates,
            vector_candidates,
        )
        results = self._apply_budgets(fused, top_k)
        log_agent_event(
            logger,
            "rag.search_completed",
            user_id=user_id,
            repository_id=repository_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            success=True,
            retrieval_chunks=len(results),
            keyword_candidates=len(keyword_candidates),
            vector_candidates=len(vector_candidates),
        )
        return results

    def _keyword_candidates(
        self,
        local_path: str,
        query: str,
    ) -> list[_Candidate]:
        matches = self.workspace_search.search_code(
            local_path,
            query=query,
            target_dir=".",
            max_results=30,
        )
        return [
            _Candidate(
                file_path=str(match["file"]),
                start_line=int(match["line"]),
                end_line=int(match["line"]),
                symbol_name=None,
                content=str(match["text"]),
            )
            for match in matches[:30]
        ]

    async def _vector_candidates(
        self,
        repository_id: int,
        query: str,
    ) -> list[_Candidate]:
        db = self.session_factory()
        try:
            chunks = list(
                db.scalars(
                    select(CodeChunk).where(
                        CodeChunk.repository_id == repository_id
                    )
                )
            )
        finally:
            db.close()

        indexed: list[tuple[CodeChunk, list[float]]] = []
        for chunk in chunks:
            try:
                vector = json.loads(chunk.embedding_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if (
                isinstance(vector, list)
                and vector
                and all(isinstance(value, (int, float)) for value in vector)
            ):
                indexed.append(
                    (chunk, [float(value) for value in vector])
                )
        if not indexed:
            return []

        query_vector = await self.embedding_provider.embed_query(query)
        if not query_vector:
            return []
        scored = [
            (
                cosine_similarity(query_vector, vector),
                chunk,
            )
            for chunk, vector in indexed
        ]
        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].file_path,
                item[1].start_line,
                item[1].end_line,
            )
        )
        return [
            _Candidate(
                file_path=chunk.file_path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                symbol_name=chunk.symbol_name,
                content=chunk.content,
            )
            for _, chunk in scored[:30]
        ]

    @staticmethod
    def _fuse(
        keyword_candidates: list[_Candidate],
        vector_candidates: list[_Candidate],
    ) -> list[RetrievedCodeChunk]:
        entries: dict[
            tuple[str, int, int],
            dict[str, object],
        ] = {}
        for rank, candidate in enumerate(
            keyword_candidates,
            start=1,
        ):
            entries[candidate.identity] = {
                "candidate": candidate,
                "keyword_rank": rank,
                "vector_rank": None,
                "score": 1.0 / (60 + rank),
            }
        for rank, candidate in enumerate(
            vector_candidates,
            start=1,
        ):
            entry = entries.get(candidate.identity)
            if entry is None:
                entries[candidate.identity] = {
                    "candidate": candidate,
                    "keyword_rank": None,
                    "vector_rank": rank,
                    "score": 1.0 / (60 + rank),
                }
                continue
            entry["candidate"] = candidate
            entry["vector_rank"] = rank
            entry["score"] = float(entry["score"]) + 1.0 / (60 + rank)

        results = [
            RetrievedCodeChunk(
                file_path=candidate.file_path,
                start_line=candidate.start_line,
                end_line=candidate.end_line,
                symbol_name=candidate.symbol_name,
                content=candidate.content,
                keyword_rank=(
                    int(entry["keyword_rank"])
                    if entry["keyword_rank"] is not None
                    else None
                ),
                vector_rank=(
                    int(entry["vector_rank"])
                    if entry["vector_rank"] is not None
                    else None
                ),
                combined_score=float(entry["score"]),
            )
            for entry in entries.values()
            for candidate in [entry["candidate"]]
        ]
        return sorted(
            results,
            key=lambda item: (
                -item.combined_score,
                item.file_path,
                item.start_line,
                item.end_line,
            ),
        )

    @staticmethod
    def _apply_budgets(
        results: list[RetrievedCodeChunk],
        top_k: int,
    ) -> list[RetrievedCodeChunk]:
        remaining = 40_000
        bounded: list[RetrievedCodeChunk] = []
        for result in results[:top_k]:
            if remaining <= 0:
                break
            content = result.content[: min(8_000, remaining)]
            bounded.append(
                result.model_copy(update={"content": content})
            )
            remaining -= len(content)
        return bounded
