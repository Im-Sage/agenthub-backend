from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base

if TYPE_CHECKING:
    from app.models.code_change import CodeChange
    from app.models.repository import Repository
    from app.models.task import Task


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code_change_id: Mapped[int] = mapped_column(ForeignKey("code_changes.id"), index=True, nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True, nullable=False)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True, nullable=False)
    branch_name: Mapped[str] = mapped_column(String(200), nullable=False)
    commit_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    pr_url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="created", index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    code_change: Mapped["CodeChange"] = relationship()
    task: Mapped["Task"] = relationship()
    repository: Mapped["Repository"] = relationship()

