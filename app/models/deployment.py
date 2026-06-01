from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base
from app.schemas.enums import DeploymentStatus

if TYPE_CHECKING:
    from app.models.code_change import CodeChange
    from app.models.task import Task


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True, nullable=False)
    code_change_id: Mapped[int] = mapped_column(ForeignKey("code_changes.id"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), default="local", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    preview_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default=DeploymentStatus.PENDING, index=True, nullable=False)
    build_logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    deploy_logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped["Task"] = relationship()
    code_change: Mapped["CodeChange"] = relationship()

