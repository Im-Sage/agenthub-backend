from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_owned_conversation, get_owned_repository
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationCreate, ConversationUpdate


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
    db.delete(conversation)
    db.commit()
