from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.conversation import Conversation
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskRead


router = APIRouter()


@router.get("", response_model=list[TaskRead])
def list_tasks(
    conversation_id: int = Query(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Task]:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")

    statement = (
        select(Task)
        .where(Task.conversation_id == conversation_id)
        .order_by(Task.created_at.asc(), Task.id.asc())
    )
    return list(db.scalars(statement))


@router.get("/{task_id}", response_model=TaskRead)
def get_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    conversation = db.get(Conversation, task.conversation_id)
    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    return task


@router.get("/{task_id}/children", response_model=list[TaskRead])
def list_child_tasks(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Task]:
    parent_task = get_task(task_id, current_user, db)
    statement = select(Task).where(Task.parent_task_id == parent_task.id).order_by(Task.id.asc())
    return list(db.scalars(statement))
