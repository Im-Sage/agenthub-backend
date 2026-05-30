from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.websocket_manager import websocket_manager
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.enums import SenderType
from app.schemas.message import MessageCreate, MessageRead, WebSocketMessageEvent
from app.services.task_service import (
    broadcast_task_event,
    create_mock_task_from_message,
    create_orchestrator_tasks_from_message,
    create_qwen_task_from_message,
)
from app.workers import agent_tasks


router = APIRouter()


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
    payload: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Message:
    conversation = ensure_owned_conversation(db, payload.conversation_id, current_user.id)
    message = Message(
        conversation_id=conversation.id,
        sender_type=SenderType.USER,
        sender_id=current_user.id,
        content=payload.content,
        message_type=payload.message_type,
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    event = WebSocketMessageEvent(data=MessageRead.model_validate(message))
    await websocket_manager.broadcast_json(conversation.id, jsonable_encoder(event))

    orchestrator_tasks = create_orchestrator_tasks_from_message(db, conversation, payload.content)
    if orchestrator_tasks is not None:
        parent_task, child_tasks = orchestrator_tasks
        await broadcast_task_event(parent_task, "task.created")
        for child_task in child_tasks:
            await broadcast_task_event(child_task, "kidstask.created")
        agent_tasks.run_orchestrator_task.delay(parent_task.id)  # 异步执行任务
        return message

    qwen_task = create_qwen_task_from_message(db, conversation, payload.content)
    if qwen_task is not None:
        await broadcast_task_event(qwen_task, "task.created")
        agent_tasks.run_agent_task.delay(qwen_task.id)
        return message

    mock_task = create_mock_task_from_message(db, conversation, payload.content)
    if mock_task is not None:
        await broadcast_task_event(mock_task, "task.created")
        agent_tasks.run_agent_task.delay(mock_task.id)

    return message


def ensure_owned_conversation(db: Session, conversation_id: int, user_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return conversation

