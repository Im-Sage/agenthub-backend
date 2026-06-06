from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base

if TYPE_CHECKING:
    from app.models.code_change import CodeChange
    from app.models.repository import Repository
    from app.models.task import Task


class CodeReview(Base):
    __tablename__ = "code_reviews"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code_change_id: Mapped[int] = mapped_column(ForeignKey("code_changes.id"), index=True, nullable=False)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True, nullable=False)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="completed", index=True, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(30), default="low", index=True, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    findings_json: Mapped[str] = mapped_column(Text, nullable=False)
    recommendations_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    code_change: Mapped["CodeChange"] = relationship()
    task: Mapped["Task"] = relationship()
    repository: Mapped["Repository"] = relationship()
