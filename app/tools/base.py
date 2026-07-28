from enum import Enum
from pydantic import BaseModel, Field
from typing import Any


class ToolRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

"""
ToolCallRequest是一个数据模型，表示调用工具的请求。它包含以下字段：
- name: 工具的名称，类型为字符串。
- arguments: 工具调用所需的参数，类型为字典，键为字符串，值为任意类型。默认值为空字典。
- task_id: 可选的  
"""
class ToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    task_id: int | None = None
    conversation_id: int | None = None
    user_id: int | None = None
    repository_id: int | None = None
    require_confirmation: bool = False


class ToolCallResult(BaseModel):
    success: bool
    content: str = ""
    structured_content: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


"""
ToolDefinition是一个数据模型，表示工具的定义。它包含以下字段：
- name: 工具的名称，类型为字符串。
- description: 工具的描述，类型为字符串。   
- risk_level: 工具的风险等级，类型为ToolRiskLevel枚举，默认值为ToolRiskLevel.LOW。
- input_schema: 工具输入参数的JSON Schema，类型为字典，键为字符串，值为任意类型。默认值为空字典。
- output_schema: 工具输出参数的JSON Schema，类型为字典，键为字符串，值为任意类型，可选字段，默认值为None。
"""
class ToolDefinition(BaseModel):
    name: str
    description: str
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
