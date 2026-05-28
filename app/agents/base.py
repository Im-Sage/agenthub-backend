from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    task_id: int
    conversation_id: int
    instruction: str
    context: dict = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    status: str
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    diff: str | None = None
    logs: str | None = None


class AgentAdapter(ABC):
    @abstractmethod
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        raise NotImplementedError

