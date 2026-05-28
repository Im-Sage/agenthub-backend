"""初始化第四阶段 Agent

Revision ID: 0003_seed_orchestrator_agents
Revises: 0002_create_agent_task_tables
Create Date: 2026-05-27 15:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003_seed_orchestrator_agents"
down_revision: str | None = "0002_create_agent_task_tables"
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
                "name": "Orchestrator Agent",
                "code": "orchestrator",
                "adapter_type": "mock",
                "system_prompt": "负责分析用户目标并拆解为多个子任务。",
                "capabilities": "需求分析、任务拆解、Agent 分派",
                "enabled": True,
            },
            {
                "name": "Backend Agent",
                "code": "backend",
                "adapter_type": "mock",
                "system_prompt": "负责后端接口、数据模型和服务逻辑。",
                "capabilities": "FastAPI、SQLAlchemy、接口设计",
                "enabled": True,
            },
            {
                "name": "Frontend Agent",
                "code": "frontend",
                "adapter_type": "mock",
                "system_prompt": "负责前端页面、交互和状态展示。",
                "capabilities": "页面设计、交互实现、接口联调",
                "enabled": True,
            },
            {
                "name": "Reviewer Agent",
                "code": "reviewer",
                "adapter_type": "mock",
                "system_prompt": "负责检查方案质量、风险和测试覆盖。",
                "capabilities": "代码审查、风险识别、测试建议",
                "enabled": True,
            },
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM agents WHERE code IN ('orchestrator', 'backend', 'frontend', 'reviewer')")

