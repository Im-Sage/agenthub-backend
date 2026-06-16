from datetime import datetime
from typing import TYPE_CHECKING # 只在“类型检查阶段”导入某些类，而在程序真正运行时不导入。

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base

if TYPE_CHECKING:
    from app.models.message import Message
    from app.models.user import User
    from app.models.repository import Repository


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    repository_id: Mapped[int | None] = mapped_column(ForeignKey("repositories.id"), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="single", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="conversations")
    repository: Mapped["Repository | None"] = relationship()


    # 一个 Conversation 可以拥有多个 Message
    # 级联删除：当会话被删除时，相关的消息也会被自动删除。
    # back_populates="conversation"：建立双向关联，
    # 与 Message 模型中的 conversation 属性互相呼应。
    # 在内存中修改其中一方时，另一方会自动保持同步

    # cascade="all, delete-orphan"：定义级联行为。
    # all：表示将父级（会话）的所有操作（如保存、更新、删除等）自动传递给子级（消息）。
    # delete-orphan：表示如果某条消息从会话的 messages 列表中被移除，或者该会话本身被删除，
    # 数据库将自动删除这些不再属于任何会话的“孤儿”消息记录。
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")

    # 可以conversation.messages访问与该会话相关的所有消息，并且当会话被删除时，相关的消息也会被自动删除，保持数据的一致性和完整性。
