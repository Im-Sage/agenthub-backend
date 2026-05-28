from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base

if TYPE_CHECKING:
    from app.models.repository import Repository
    from app.models.task import Task


class CodeChange(Base):
    __tablename__ = "code_changes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True, nullable=False)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True, nullable=False)
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(200), nullable=False)
    commit_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    changed_files: Mapped[str] = mapped_column(Text, nullable=False)
    diff_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="generated", index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped["Task"] = relationship()
    repository: Mapped["Repository"] = relationship(back_populates="code_changes")

