from datetime import datetime

from pydantic import BaseModel, Field

# 定义消息创建请求的验证模型，
# 包含 conversation_id、content 和 message_type 字段，
# 并对 content 的长度和 message_type 的取值范围进行约束。
class MessageCreate(BaseModel): # 前端发送到后端的数据结构
    conversation_id: int
    content: str = Field(min_length=1)
    message_type: str = Field(default="text", pattern="^(text|task|diff|deploy)$")


# 定义消息读取响应的模型，包含了消息的所有字段，
# 并通过 model_config 设置了 from_attributes=True，
# 使得 Pydantic 可以直接从 SQLAlchemy 模型实例中读取属性值进行序列化。
# 这使得我们在从数据库查询到消息记录后，
# 可以直接将 SQLAlchemy 模型实例传递给 MessageRead 模型进行序列化，
# 而不需要手动提取每个字段的值。这简化了代码并提高了开发效率。
class MessageRead(BaseModel): # 响应给前端的数据结构
    id: int
    conversation_id: int
    sender_type: str
    sender_id: int | None
    content: str
    message_type: str
    created_at: datetime

    # 允许直接把 SQLAlchemy 模型实例序列化成接口响应。
    model_config = {"from_attributes": True}

# 定义一个事件模型，用于 WebSocket 通信，包含事件类型和消息数据
class WebSocketMessageEvent(BaseModel):
    event: str = "message.created"
    data: MessageRead

