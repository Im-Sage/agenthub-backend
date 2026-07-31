from abc import ABC, abstractmethod

from typing import Any

from pydantic import BaseModel, Field


# AgentRunRequest 定义了一个数据模型，
# 包含了运行 Agent 所需的各种信息，
# 如任务 ID、会话 ID、指令、代码仓库路径、分支名称、目标文件列表、上下文信息等。
# context 字段是一个字典，用于存储与运行 Agent 相关的上下文信息，
# 例如用户信息、环境变量、配置参数等。它可以在运行过程中传递额外的数据，以便 Agent 根据这些信息做出更智能的决策。
# task 字段是一个可选的任意类型字段，用于存储与任务相关的额外信息，例如任务的元数据、状态、优先级等。它可以在运行过程中
class AgentRunRequest(BaseModel):
    task_id: int
    conversation_id: int
    instruction: str
    repo_path: str | None = None
    repository_id: int | None = None
    user_id: int | None = None
    branch_name: str | None = None
    target_files: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    task: Any | None = None


class AgentRunResult(BaseModel):
    status: str
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    diff: str | None = None
    logs: str | None = None

# ABC（Abstract Base Class）是 Python 标准库中的一个模块，用于定义抽象基类。
# 抽象基类是一种不能被实例化的类，通常用于定义接口或规范，要求子类必须实现特定的方法。
# 在这个代码中，AgentAdapter 被定义为一个抽象基类，要求所有继承自它的类必须实现 run 方法。
# 这种设计有助于确保所有 AgentAdapter 的子类都遵循相同的接口规范，使得代码更加一致和可维护。
# 这里ABC的作用是把它定义成一个抽象基类，要求所有继承自 AgentAdapter 的类必须实现 run 方法。
class AgentAdapter(ABC):
    @abstractmethod
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        raise RuntimeError(
            "Concrete AgentAdapter classes must implement run()"
        )

