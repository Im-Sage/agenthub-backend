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

"""
RepositoryResolver 是一个用于解析和验证用户对仓库工作区访问权限的类。
它通过提供仓库 ID 和用户 ID 来检查用户是否有权访问指定的仓库，并返回相关的工作区信息。
主要功能：
1. 初始化：接受一个可调用的 session_factory，用于创建数据库会话。
2. resolve_owned_workspace 方法：
   - 输入：仓库 ID 和用户 ID。
   - 验证输入的仓库 ID 和用户 ID 是否为正整数。
   - 查询数据库以获取指定的仓库信息。
   - 检查仓库是否存在，用户是否有访问权限，以及工作区路径是否配置和存在。
   - 返回 ResolvedWorkspace 对象，包含仓库 ID、用户 ID 和本地路径。
   - 如果验证失败，抛出 WorkspaceAuthorizationError 异常，提供详细的错误信息。
"""
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
