from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_conversation
from app.core.config import settings
from app.core.rate_limit import rate_limiter
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.enums import SenderType
from app.schemas.message import MessageCreate, MessageRead
from app.services import event_service
from app.services.task_service import (
    broadcast_task_event,
    create_mock_task_from_message,
    create_orchestrator_tasks_from_message,
    create_qwen_task_from_message,
    ensure_user_task_capacity,
    parse_mock_instruction,
    parse_orchestrator_goal,
    parse_qwen_instruction,
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
    get_owned_conversation(db, conversation_id, current_user.id)
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
    conversation = get_owned_conversation(db, payload.conversation_id, current_user.id)
    rate_limiter.hit(
        f"message:{current_user.id}",
        limit=settings.message_rate_limit_count,
        window_seconds=settings.message_rate_limit_window_seconds,
    )
    if (
        parse_orchestrator_goal(payload.content) is not None
        or parse_qwen_instruction(payload.content) is not None
        or parse_mock_instruction(payload.content) is not None
    ):
        ensure_user_task_capacity(db, current_user.id)

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

    await event_service.publish_message_event(message)

    orchestrator_task = create_orchestrator_tasks_from_message(db, conversation, payload.content)
    if orchestrator_task is not None:
        # 确保提交任务到数据库后再发送广播
        db.commit()
        await broadcast_task_event(orchestrator_task, "task.created")
        result = agent_tasks.run_orchestrator_task.delay(orchestrator_task.id)
        # 保存 Celery ID 到数据库以便后续取消
        orchestrator_task.celery_task_id = result.id
        db.commit()
        return message

    qwen_task = create_qwen_task_from_message(db, conversation, payload.content)
    if qwen_task is not None:
        db.commit()
        await broadcast_task_event(qwen_task, "task.created")
        result = agent_tasks.run_agent_task.delay(qwen_task.id)
        qwen_task.celery_task_id = result.id
        db.commit()
        return message

    mock_task = create_mock_task_from_message(db, conversation, payload.content)
    if mock_task is not None:
        db.commit()
        await broadcast_task_event(mock_task, "task.created")
        result = agent_tasks.run_agent_task.delay(mock_task.id)
        mock_task.celery_task_id = result.id
        db.commit()

    return message

