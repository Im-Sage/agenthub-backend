from enum import Enum
from pydantic import BaseModel, Field
from typing import Any


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    task_id: int | None = None
    conversation_id: int | None = None
    user_id: int | None = None
    require_confirmation: bool = False


class ToolCallResult(BaseModel):
    success: bool
    content: str = ""
    structured_content: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
