from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

"""
定义了一个抽象基类 AgentAdapter，
以及两个 Pydantic 模型 AgentRunRequest 和 AgentRunResult，
用于描述智能体运行的输入和输出数据结构。
"""
# AgentRunRequest 定义了智能体运行所需的参数，包括任务 ID、对话 ID、指令文本和上下文信息。
class AgentRunRequest(BaseModel):
    task_id: int
    conversation_id: int
    instruction: str
    context: dict = Field(default_factory=dict)

# AgentRunResult 定义了智能体运行的结果，包括状态、摘要、修改的文件列表、差异信息和日志信息。
class AgentRunResult(BaseModel):
    status: str
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    diff: str | None = None
    logs: str | None = None

# AgentAdapter 是一个抽象基类，定义了一个抽象方法 run，要求所有继承该类的具体智能体适配器必须实现这个方法，以便处理智能体运行的请求并返回结果。
class AgentAdapter(ABC):
    @abstractmethod
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        raise NotImplementedError

