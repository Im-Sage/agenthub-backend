from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base
from app.schemas.enums import AgentAdapterType

"""
Agent模型参数定义
- id: 主键，唯一标识一个Agent
- name: Agent的名称，不能为空
- code: Agent的唯一代码标识，不能为空且必须唯一
- adapter_type: Agent使用的适配器类型，默认为"mock"，不能为空
- system_prompt: Agent的系统提示信息，可以为空
- capabilities: Agent的能力描述，可以为空
- enabled: Agent是否启用，默认为True，不能为空
"""
class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(50), default=AgentAdapterType.MOCK, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    capabilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tasks: Mapped[list["Task"]] = relationship(back_populates="agent")

