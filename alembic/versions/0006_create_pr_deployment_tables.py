"""创建 PR 和预览部署表

Revision ID: 0006_create_pr_deployment_tables
Revises: 0005_seed_qwen_agent
Create Date: 2026-05-28 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_create_pr_deployment_tables"
down_revision: str | None = "0005_seed_qwen_agent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code_change_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("branch_name", sa.String(length=200), nullable=False),
        sa.Column("commit_hash", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("pr_url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["code_change_id"], ["code_changes.id"]),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pull_requests_id"), "pull_requests", ["id"], unique=False)
    op.create_index(op.f("ix_pull_requests_code_change_id"), "pull_requests", ["code_change_id"], unique=False)
    op.create_index(op.f("ix_pull_requests_task_id"), "pull_requests", ["task_id"], unique=False)
    op.create_index(op.f("ix_pull_requests_repository_id"), "pull_requests", ["repository_id"], unique=False)
    op.create_index(op.f("ix_pull_requests_status"), "pull_requests", ["status"], unique=False)

    op.create_table(
        "deployments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("code_change_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("preview_url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("logs", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["code_change_id"], ["code_changes.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_deployments_id"), "deployments", ["id"], unique=False)
    op.create_index(op.f("ix_deployments_task_id"), "deployments", ["task_id"], unique=False)
    op.create_index(op.f("ix_deployments_code_change_id"), "deployments", ["code_change_id"], unique=False)
    op.create_index(op.f("ix_deployments_status"), "deployments", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_deployments_status"), table_name="deployments")
    op.drop_index(op.f("ix_deployments_code_change_id"), table_name="deployments")
    op.drop_index(op.f("ix_deployments_task_id"), table_name="deployments")
    op.drop_index(op.f("ix_deployments_id"), table_name="deployments")
    op.drop_table("deployments")
    op.drop_index(op.f("ix_pull_requests_status"), table_name="pull_requests")
    op.drop_index(op.f("ix_pull_requests_repository_id"), table_name="pull_requests")
    op.drop_index(op.f("ix_pull_requests_task_id"), table_name="pull_requests")
    op.drop_index(op.f("ix_pull_requests_code_change_id"), table_name="pull_requests")
    op.drop_index(op.f("ix_pull_requests_id"), table_name="pull_requests")
    op.drop_table("pull_requests")

