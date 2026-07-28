from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.repository import Repository


class WorkspaceAuthorizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedWorkspace:
    repository_id: int
    user_id: int
    local_path: str


class RepositoryResolver:
    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
    ):
        self._session_factory = session_factory

    def resolve_owned_workspace(
        self,
        repository_id: int,
        user_id: int,
    ) -> ResolvedWorkspace:
        if repository_id <= 0 or user_id <= 0:
            raise WorkspaceAuthorizationError(
                "repository_id and user_id must be positive integers"
            )

        db = self._session_factory()
        try:
            repository = db.get(Repository, repository_id)
            if repository is None:
                raise WorkspaceAuthorizationError("Repository not found")
            if repository.user_id != user_id:
                raise WorkspaceAuthorizationError(
                    "Repository access denied"
                )
            if not repository.local_path:
                raise WorkspaceAuthorizationError(
                    "Repository workspace is not configured"
                )
            if not Path(repository.local_path).is_dir():
                raise WorkspaceAuthorizationError(
                    "Repository workspace directory does not exist"
                )

            return ResolvedWorkspace(
                repository_id=repository.id,
                user_id=repository.user_id,
                local_path=repository.local_path,
            )
        finally:
            db.close()
