from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskRead
from app.services import task_service


router = APIRouter()

# * GET /api/tasks?conversation_id=1：查询属于会话 1 的所有任务列表
@router.get("", response_model=list[TaskRead])
def list_tasks(
    conversation_id: int = Query(), # 此处Query是查询参数，不是路径参数
    #  conversation_id 被定义为 int = Query()
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Task]:
    return task_service.list_tasks(db, current_user.id, conversation_id)


@router.get("/{task_id}", response_model=TaskRead)
def get_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    return task_service.get_task(db, current_user.id, task_id)


@router.get("/{task_id}/children", response_model=list[TaskRead])
def list_child_tasks(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Task]:
    return task_service.list_child_tasks(db, current_user.id, task_id)
