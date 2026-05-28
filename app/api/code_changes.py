from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.websocket_manager import websocket_manager
from app.db.session import get_db
from app.models.code_change import CodeChange
from app.models.conversation import Conversation
from app.models.task import Task
from app.models.user import User
from app.schemas.code_change import CodeChangeEvent, CodeChangeGenerate, CodeChangeRead
from app.services.repo_service import generate_code_change, get_owned_repository


router = APIRouter()


@router.post("/generate", response_model=CodeChangeRead, status_code=status.HTTP_201_CREATED)
async def generate_task_code_change(
    payload: CodeChangeGenerate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CodeChange:
    task = get_owned_task(db, payload.task_id, current_user.id)
    repository = get_owned_repository(db, payload.repository_id, current_user.id)
    code_change = generate_code_change(db, task, repository)
    event = CodeChangeEvent(data=CodeChangeRead.model_validate(code_change))
    await websocket_manager.broadcast_json(task.conversation_id, jsonable_encoder(event))
    return code_change


@router.get("/{task_id}", response_model=list[CodeChangeRead])
def list_task_code_changes(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CodeChange]:
    get_owned_task(db, task_id, current_user.id)
    statement = (
        select(CodeChange)
        .where(CodeChange.task_id == task_id)
        .order_by(CodeChange.created_at.desc(), CodeChange.id.desc())
    )
    return list(db.scalars(statement))


def get_owned_task(db: Session, task_id: int, user_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    conversation = db.get(Conversation, task.conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    return task


def get_owned_code_change(db: Session, code_change_id: int, user_id: int) -> CodeChange:
    code_change = db.get(CodeChange, code_change_id)
    if code_change is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="代码变更不存在")

    get_owned_task(db, code_change.task_id, user_id)
    return code_change

