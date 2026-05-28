from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.websocket_manager import websocket_manager
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.message import MessageCreate, MessageRead, WebSocketMessageEvent
from app.services.task_service import broadcast_task_event, create_mock_task_from_message, run_mock_agent_task


router = APIRouter()

"""
消息相关的 API 路由设计主要包括两个核心功能：列出对话中的消息和创建新消息。以下是这两个功能的设计考虑：
1. 列出消息（GET /conversations/{conversation_id}/messages）：
- 访问控制：确保只有对话的拥有者才能访问该对话中的消息。通过验证当前用户与对话的关联关系，防止未授权访问。
- 分页支持：通过 limit 和 offset 参数实现分页功能，允许客户端控制每次请求返回的消息数量和起始位置，提升性能和用户体验。
- 数据排序：按照消息的创建时间和 ID 进行排序，确保消息以正确的顺序返回，方便客户端展示。
2. 创建消息（POST /messages）：
- 访问控制：同样确保只有对话的拥有者才能在该对话中创建消息。通过验证当前用户与对话的关联关系，防止未授权操作。
- 数据验证：使用 Pydantic 模型（MessageCreate）验证请求体中的数据，确保消息内容和类型符合预期。
- 实时更新：在成功创建消息后，使用 WebSocketManager 向所有连接到该对话的客户端广播新消息事件，实现实时更新的功能，提升用户体验。
- 数据库操作：在创建消息时，确保正确处理数据库事务，添加新消息并刷新以获取数据库生成的 ID 和时间戳。
总的来说，这些 API 路由的设计旨在提供一个安全、可靠且用户友好的消息管理机制，确保只有授权用户能够访问和操作消息，同时通过分页和实时更新提升性能和用户体验。
"""
@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages(
    conversation_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Message]:
    ensure_owned_conversation(db, conversation_id, current_user.id)
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(statement))


@router.post("/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    payload: MessageCreate, # 请求体参数，包含 conversation_id、content 和 message_type，用于创建新消息
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Message:
    conversation = ensure_owned_conversation(db, payload.conversation_id, current_user.id)
    message = Message(
        conversation_id=conversation.id,
        sender_type="user",
        sender_id=current_user.id,
        content=payload.content,
        message_type=payload.message_type,
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    # 创建消息后，使用 WebSocketManager 向所有连接到该对话的客户端广播新消息事件，实现实时更新的功能，提升用户体验。
    event = WebSocketMessageEvent(data=MessageRead.model_validate(message)) # 将 SQLAlchemy 模型实例转换为 Pydantic 模型实例，以便序列化成接口响应。
    await websocket_manager.broadcast_json(conversation.id, jsonable_encoder(event))

    # 如果消息类型是 "task"，则创建一个模拟任务并在后台运行，以演示任务事件的广播和处理。
    task = create_mock_task_from_message(db, conversation, payload.content)
    if task is not None:
        await broadcast_task_event(task, "task.created")
        background_tasks.add_task(run_mock_agent_task, task.id)

    return message


def ensure_owned_conversation(db: Session, conversation_id: int, user_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return conversation
