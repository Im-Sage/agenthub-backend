"""add code chunks

Revision ID: 0012_add_code_chunks
Revises: 90fab20b872e
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012_add_code_chunks"
down_revision: str | None = "90fab20b872e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "code_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("symbol_name", sa.String(length=500), nullable=True),
        sa.Column("chunk_type", sa.String(length=50), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("commit_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "embedding_json",
            sa.Text(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id",
            "file_path",
            "content_hash",
            "start_line",
            "end_line",
            name="uq_code_chunks_repository_file_content_range",
        ),
    )
    op.create_index(
        "ix_code_chunks_repository_id",
        "code_chunks",
        ["repository_id"],
        unique=False,
    )
    op.create_index(
        "ix_code_chunks_repository_file",
        "code_chunks",
        ["repository_id", "file_path"],
        unique=False,
    )
    op.create_index(
        "ix_code_chunks_repository_content_hash",
        "code_chunks",
        ["repository_id", "content_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_code_chunks_repository_content_hash",
        table_name="code_chunks",
    )
    op.drop_index(
        "ix_code_chunks_repository_file",
        table_name="code_chunks",
    )
    op.drop_index(
        "ix_code_chunks_repository_id",
        table_name="code_chunks",
    )
    op.drop_table("code_chunks")
