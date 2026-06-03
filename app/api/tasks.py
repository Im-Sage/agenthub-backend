from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_task
from app.db.session import get_db
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskRead
from app.schemas.enums import TaskStatus
from app.services import task_service
from app.workers.celery_app import celery_app


router = APIRouter()


# * GET /api/tasks?conversation_id=1：查询属于会话 1 的所有任务列表
@router.get("", response_model=list[TaskRead])
def list_tasks(
    conversation_id: int = Query(),
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
    return get_owned_task(db, task_id, current_user.id)


@router.get("/{task_id}/children", response_model=list[TaskRead])
def list_child_tasks(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Task]:
    get_owned_task(db, task_id, current_user.id)
    return task_service.list_child_tasks(db, current_user.id, task_id)


@router.post("/{task_id}/cancel", response_model=TaskRead)
async def cancel_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    task = get_owned_task(db, task_id, current_user.id)
    
    if task.status not in [TaskStatus.PENDING, TaskStatus.RUNNING]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"无法取消状态为 {task.status} 的任务"
        )
        
    # 1. 寻找可撤销的 Celery ID（如果自己没有，就找父任务）
    target_celery_id = task.celery_task_id
    current_t = task
    while not target_celery_id and current_t.parent_task_id:
        parent = db.get(Task, current_t.parent_task_id)
        if not parent: break
        target_celery_id = parent.celery_task_id
        current_t = parent

    # 2. 撤销 Celery 任务
    if target_celery_id:
        celery_app.control.revoke(target_celery_id, terminate=True)
        
    # 3. 将自己和所有关联任务标记为已取消（软拦截）
    task.status = TaskStatus.CANCELLED
    
    # 如果是父任务，要把所有正在排队的子任务也标记掉
    if not task.parent_task_id:
        from sqlalchemy import update
        db.execute(
            update(Task)
            .where(Task.parent_task_id == task.id)
            .where(Task.status == TaskStatus.PENDING)
            .values(status=TaskStatus.CANCELLED)
        )
        
    db.commit()
    db.refresh(task)
    
    # 4. 广播
    await task_service.broadcast_task_event(task, "task.updated")
    
    return task
