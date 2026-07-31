from datetime import datetime

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_task
from app.db.session import get_db
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskPlanRead, TaskRead
from app.schemas.enums import TaskStatus
from app.services import task_service
from app.services import orchestrator_recovery_service
from app.workers import agent_tasks
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


@router.get("/{task_id}/plan", response_model=TaskPlanRead)
def get_task_plan(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    task = get_owned_task(db, task_id, current_user.id)
    return task_service.get_orchestrator_plan(task)


@router.post("/{task_id}/plan/confirm", response_model=TaskRead)
async def confirm_task_plan(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    task = get_owned_task(db, task_id, current_user.id)
    task_service.ensure_user_task_capacity(db, current_user.id)
    task = task_service.confirm_orchestrator_plan(db, task)

    # 恢复已中断的 Orchestrator，并保存新的 Celery 任务 ID 以便后续取消
    result = agent_tasks.resume_orchestrator_task.delay(
        task.id,
        {"approved": True},
    )
    task.celery_task_id = result.id
    db.commit()
    db.refresh(task)
    await task_service.broadcast_task_event(task, "task.updated")
    return task


@router.post("/{task_id}/cancel", response_model=TaskRead)
async def cancel_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    task = get_owned_task(db, task_id, current_user.id)

    if task.parent_task_id is None and task_service.is_orchestrator_task(task):
        result = orchestrator_recovery_service.cancel_orchestrator(task.id)
        if result.get("status") != "cancelled":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error") or "Unable to cancel orchestrator",
            )
        db.expire_all()
        task = get_owned_task(db, task_id, current_user.id)
        await task_service.broadcast_task_event(task, "task.updated")
        return task
    
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
    task.finished_at = datetime.utcnow()
    
    # 如果是父任务，要把所有正在排队的子任务也标记掉
    if not task.parent_task_id:
        from sqlalchemy import update
        db.execute(
            update(Task)
            .where(Task.parent_task_id == task.id)
            .where(Task.status == TaskStatus.PENDING)
            .values(status=TaskStatus.CANCELLED, finished_at=datetime.utcnow())
        )
        
    db.commit()
    db.refresh(task)
    
    # 4. 广播
    await task_service.broadcast_task_event(task, "task.updated")
    
    return task


"""
retry_task 为重试任务的接口，主要逻辑如下：
1. 验证用户是否有权限访问指定的任务。
2. 检查用户的任务容量是否足够，确保用户可以创建新的任务。
3. 创建一个新的重试任务，并将其与原任务关联。
4. 广播任务创建事件，通知前端或其他服务有新任务。
5. 根据任务的适配器类型，将任务提交到 Celery 队列执行，并保存 Celery 任务 ID 以便后续取消。
6. 提交数据库事务并刷新重试任务对象。
7. 广播任务更新事件，通知前端或其他服务任务状态已更新。
8. 返回创建的重试任务对象。
"""
@router.post("/{task_id}/retry", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def retry_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    # 1. 验证用户是否有权限访问指定的任务,task_id 是要重试的任务的 ID，current_user 是当前登录的用户，db 是数据库会话
    task = get_owned_task(db, task_id, current_user.id)
    task_service.ensure_user_task_capacity(db, current_user.id)
    if task.parent_task_id is None and task_service.is_orchestrator_task(task):
        orchestrator_recovery_service.retry_failed_orchestrator(task.id)
        db.expire_all()
        retried = get_owned_task(db, task_id, current_user.id)
        await task_service.broadcast_task_event(retried, "task.updated")
        return retried
    # 2. 创建一个新的重试任务，并将其与原任务关联
    retry_task = task_service.create_retry_task(db, task)
    await task_service.broadcast_task_event(retry_task, "task.created")

    if retry_task.agent.adapter_type == "langgraph":
        result = agent_tasks.run_orchestrator_task.delay(retry_task.id)
    else:
        result = agent_tasks.run_agent_task.delay(retry_task.id)

    retry_task.celery_task_id = result.id
    db.commit()
    db.refresh(retry_task)
    await task_service.broadcast_task_event(retry_task, "task.updated")
    return retry_task


@router.post("/{task_id}/reconcile")
def reconcile_orchestrator_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    task = get_owned_task(db, task_id, current_user.id)
    if not task_service.is_orchestrator_task(task):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task is not an orchestrator",
        )
    return orchestrator_recovery_service.reconcile_orchestrator(task.id)


@router.post("/{task_id}/cleanup")
def cleanup_orchestrator_task(
    task_id: int,
    force: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    task = get_owned_task(db, task_id, current_user.id)
    if not task_service.is_orchestrator_task(task):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task is not an orchestrator",
        )
    return orchestrator_recovery_service.cleanup_terminal_orchestrator(
        task.id,
        force=force,
    )
