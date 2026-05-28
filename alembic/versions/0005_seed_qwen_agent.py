"""初始化阿里通义千问 Agent

Revision ID: 0005_seed_qwen_agent
Revises: 0004_create_repo_code_change_tables
Create Date: 2026-05-28 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0005_seed_qwen_agent"
down_revision: str | None = "0004_create_repo_code_change_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


agents_table = sa.table(
    "agents",
    sa.column("name", sa.String),
    sa.column("code", sa.String),
    sa.column("adapter_type", sa.String),
    sa.column("system_prompt", sa.Text),
    sa.column("capabilities", sa.Text),
    sa.column("enabled", sa.Boolean),
)


def upgrade() -> None:
    op.bulk_insert(
        agents_table,
        [
            {
                "name": "Qwen Agent",
                "code": "qwen",
                "adapter_type": "aliyun_qwen",
                "system_prompt": "使用阿里通义千问处理真实 LLM 任务。",
                "capabilities": "真实 LLM 回复、需求分析、方案生成",
                "enabled": True,
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM agents WHERE code = 'qwen'")

