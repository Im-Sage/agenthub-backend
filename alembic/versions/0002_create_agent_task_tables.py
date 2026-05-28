"""创建 Agent 和任务表

Revision ID: 0002_create_agent_task_tables
Revises: 0001_create_first_stage_tables
Create Date: 2026-05-27 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0002_create_agent_task_tables"
down_revision: str | None = "0001_create_first_stage_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("adapter_type", sa.String(length=50), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("capabilities", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agents_id"), "agents", ["id"], unique=False)
    op.create_index(op.f("ix_agents_code"), "agents", ["code"], unique=True)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("parent_task_id", sa.Integer(), nullable=True),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["parent_task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_id"), "tasks", ["id"], unique=False)
    op.create_index(op.f("ix_tasks_conversation_id"), "tasks", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_tasks_agent_id"), "tasks", ["agent_id"], unique=False)
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)

    op.bulk_insert(
        sa.table(
            "agents",
            sa.column("name", sa.String),
            sa.column("code", sa.String),
            sa.column("adapter_type", sa.String),
            sa.column("system_prompt", sa.Text),
            sa.column("capabilities", sa.Text),
            sa.column("enabled", sa.Boolean),
        ),
        [
            {
                "name": "Mock Agent",
                "code": "mock",
                "adapter_type": "mock",
                "system_prompt": "用于第三阶段联调的模拟 Agent。",
                "capabilities": "任务回显、流程联调、WebSocket 推送验证",
                "enabled": True,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_agent_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_conversation_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_id"), table_name="tasks")
    op.drop_table("tasks")
    op.drop_index(op.f("ix_agents_code"), table_name="agents")
    op.drop_index(op.f("ix_agents_id"), table_name="agents")
    op.drop_table("agents")

