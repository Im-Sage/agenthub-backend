from enum import Enum
from typing import Any

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field


class ContextSource(str, Enum):
    SYSTEM = "system"
    CURRENT_REQUEST = "current_request"
    CONVERSATION = "conversation"
    REPOSITORY = "repository"
    RETRIEVAL = "retrieval"
    EXECUTION_RESULT = "execution_result"
    ERROR = "error"


class ContextBlock(BaseModel):
    source: ContextSource
    content: str
    priority: int
    estimated_tokens: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssembledAgentContext(BaseModel):
    blocks: list[ContextBlock]
    messages: list[BaseMessage]
    estimated_tokens: int
    truncated_blocks: list[dict[str, Any]]
