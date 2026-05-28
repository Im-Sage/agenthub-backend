"""创建仓库和代码变更表

Revision ID: 0004_create_repo_code_change_tables
Revises: 0003_seed_orchestrator_agents
Create Date: 2026-05-27 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0004_create_repo_code_change_tables"
down_revision: str | None = "0003_seed_orchestrator_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("repo_url", sa.String(length=500), nullable=False),
        sa.Column("local_path", sa.String(length=500), nullable=False),
        sa.Column("default_branch", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_repositories_id"), "repositories", ["id"], unique=False)
    op.create_index(op.f("ix_repositories_user_id"), "repositories", ["user_id"], unique=False)

    op.create_table(
        "code_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("repo_url", sa.String(length=500), nullable=False),
        sa.Column("branch_name", sa.String(length=200), nullable=False),
        sa.Column("commit_hash", sa.String(length=100), nullable=True),
        sa.Column("changed_files", sa.Text(), nullable=False),
        sa.Column("diff_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_code_changes_id"), "code_changes", ["id"], unique=False)
    op.create_index(op.f("ix_code_changes_task_id"), "code_changes", ["task_id"], unique=False)
    op.create_index(op.f("ix_code_changes_repository_id"), "code_changes", ["repository_id"], unique=False)
    op.create_index(op.f("ix_code_changes_status"), "code_changes", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_code_changes_status"), table_name="code_changes")
    op.drop_index(op.f("ix_code_changes_repository_id"), table_name="code_changes")
    op.drop_index(op.f("ix_code_changes_task_id"), table_name="code_changes")
    op.drop_index(op.f("ix_code_changes_id"), table_name="code_changes")
    op.drop_table("code_changes")
    op.drop_index(op.f("ix_repositories_user_id"), table_name="repositories")
    op.drop_index(op.f("ix_repositories_id"), table_name="repositories")
    op.drop_table("repositories")

