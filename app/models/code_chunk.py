from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class CodeChunk(Base):
    __tablename__ = "code_chunks"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "file_path",
            "content_hash",
            "start_line",
            "end_line",
            name="uq_code_chunks_repository_file_content_range",
        ),
        Index(
            "ix_code_chunks_repository_file",
            "repository_id",
            "file_path",
        ),
        Index(
            "ix_code_chunks_repository_content_hash",
            "repository_id",
            "content_hash",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    language: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol_name: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    chunk_type: Mapped[str] = mapped_column(String(50), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    embedding_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default="[]",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
