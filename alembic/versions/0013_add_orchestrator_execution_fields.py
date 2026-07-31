"""add orchestrator execution fields

Revision ID: 0013_add_orchestrator_execution_fields
Revises: 0012_add_code_chunks
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013_add_orchestrator_execution_fields"
down_revision: str | None = "0012_add_code_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("step_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("step_index", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("wave_index", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("write_scope_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("worktree_path", sa.Text(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("branch_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("base_commit_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("result_commit_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("merge_status", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("verification_result_json", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_tasks_parent_task_id",
        "tasks",
        ["parent_task_id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_parent_step_key",
        "tasks",
        ["parent_task_id", "step_key"],
        unique=True,
    )
    op.create_index(
        "ix_tasks_parent_wave_index",
        "tasks",
        ["parent_task_id", "wave_index"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tasks_parent_wave_index",
        table_name="tasks",
    )
    op.drop_index(
        "ix_tasks_parent_step_key",
        table_name="tasks",
    )
    op.drop_index(
        "ix_tasks_parent_task_id",
        table_name="tasks",
    )
    op.drop_column("tasks", "verification_result_json")
    op.drop_column("tasks", "merge_status")
    op.drop_column("tasks", "result_commit_hash")
    op.drop_column("tasks", "base_commit_hash")
    op.drop_column("tasks", "branch_name")
    op.drop_column("tasks", "worktree_path")
    op.drop_column("tasks", "write_scope_json")
    op.drop_column("tasks", "wave_index")
    op.drop_column("tasks", "step_index")
    op.drop_column("tasks", "step_key")
