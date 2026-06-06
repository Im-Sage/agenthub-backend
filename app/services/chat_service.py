from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_owned_conversation, get_owned_repository
from app.models.code_change import CodeChange
from app.models.code_review import CodeReview
from app.models.conversation import Conversation
from app.models.deployment import Deployment
from app.models.message import Message
from app.models.pull_request import PullRequest
from app.models.task import Task
from app.schemas.conversation import ConversationCreate, ConversationUpdate
from app.workers.celery_app import celery_app


def create_conversation(db: Session, user_id: int, payload: ConversationCreate) -> Conversation:
    # 验证如果传了 repository_id，该仓库必须属于该用户
    if payload.repository_id is not None:
        get_owned_repository(db, payload.repository_id, user_id)

    conversation = Conversation(
        user_id=user_id, 
        repository_id=payload.repository_id,
        title=payload.title, 
        type=payload.type
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def list_conversations(db: Session, user_id: int) -> list[Conversation]:
    statement = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(db.scalars(statement))


def update_conversation(db: Session, user_id: int, conversation_id: int, payload: ConversationUpdate) -> Conversation:
    conversation = get_owned_conversation(db, conversation_id, user_id)
    conversation.title = payload.title
    db.commit()
    db.refresh(conversation)
    return conversation


def delete_conversation(db: Session, user_id: int, conversation_id: int) -> None:
    conversation = get_owned_conversation(db, conversation_id, user_id)
    task_ids = list(
        db.scalars(select(Task.id).where(Task.conversation_id == conversation.id))
    )

    if task_ids:
        celery_task_ids = list(
            db.scalars(
                select(Task.celery_task_id)
                .where(Task.id.in_(task_ids))
                .where(Task.celery_task_id.is_not(None))
            )
        )
        for celery_task_id in celery_task_ids:
            celery_app.control.revoke(celery_task_id, terminate=True)

        code_change_ids = list(
            db.scalars(select(CodeChange.id).where(CodeChange.task_id.in_(task_ids)))
        )

        if code_change_ids:
            db.execute(delete(CodeReview).where(CodeReview.code_change_id.in_(code_change_ids)))
            db.execute(delete(PullRequest).where(PullRequest.code_change_id.in_(code_change_ids)))
            db.execute(delete(Deployment).where(Deployment.code_change_id.in_(code_change_ids)))

        db.execute(delete(PullRequest).where(PullRequest.task_id.in_(task_ids)))
        db.execute(delete(Deployment).where(Deployment.task_id.in_(task_ids)))
        db.execute(delete(CodeChange).where(CodeChange.task_id.in_(task_ids)))

        db.execute(
            update(Task)
            .where(Task.conversation_id == conversation.id)
            .values(parent_task_id=None)
        )
        db.execute(delete(Task).where(Task.conversation_id == conversation.id))

    db.execute(delete(Message).where(Message.conversation_id == conversation.id))
    db.delete(conversation)
    db.commit()
