from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base
from app.schemas.enums import TaskStatus

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.conversation import Conversation


"""
Task 模型表示一个任务，通常由一个 Agent 执行，
可能属于一个 Conversation，
并且可以有一个父任务（用于表示任务之间的层级关系）。
它包含了任务的状态、指令、结果摘要和错误信息等字段，
以便跟踪任务的执行情况和结果。
"""
class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_parent_task_id", "parent_task_id"),
        Index(
            "ix_tasks_parent_step_key",
            "parent_task_id",
            "step_key",
            unique=True,
        ),
        Index(
            "ix_tasks_parent_wave_index",
            "parent_task_id",
            "wave_index",
        ),
    )
    """
    当你实例化这个模型时，该属性对应的 Python 类型是什么。
    例如，Mapped[int] 表示这个属性在 Python 代码中会被当作 int 类型处理
    """
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True, nullable=False)
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default=TaskStatus.PENDING, index=True, nullable=False)
    task_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    depends_on: Mapped[str | None] = mapped_column(Text, nullable=True)  # 存储 JSON 数组，如 "[1, 2]"
    step_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wave_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    write_scope_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merge_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    verification_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 存储任务元数据
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    conversation: Mapped["Conversation"] = relationship()
    agent: Mapped["Agent"] = relationship(back_populates="tasks")
    parent_task: Mapped["Task | None"] = relationship(remote_side=[id])

