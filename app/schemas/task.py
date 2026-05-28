from datetime import datetime

from pydantic import BaseModel


# 验证并格式化从**后端传到客户端（前端）**的数据
class TaskRead(BaseModel):
    id: int
    conversation_id: int
    parent_task_id: int | None
    agent_id: int
    status: str
    instruction: str
    result_summary: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# 定义一个事件模型，用于 WebSocket 通信，包含事件类型和任务数据
class TaskEvent(BaseModel):
    event: str
    data: TaskRead

