from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_code_change, get_owned_task, get_owned_repository
from app.db.session import get_db
from app.models.code_change import CodeChange
from app.models.code_review import CodeReview
from app.models.conversation import Conversation
from app.models.task import Task
from app.models.user import User
from app.schemas.code_change import CodeChangeGenerate, CodeChangeRead, CodeChangeReject
from app.schemas.code_review import CodeReviewRead
from app.schemas.task import TaskRead
from app.services import code_change_service, code_review_service, event_service, task_service
from app.services.repo_service import generate_code_change
from app.workers import agent_tasks


router = APIRouter()


@router.post("/generate", response_model=CodeChangeRead, status_code=status.HTTP_201_CREATED)
async def generate_task_code_change(
    payload: CodeChangeGenerate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CodeChange:
    task = get_owned_task(db, payload.task_id, current_user.id)
    
    # 强制优先使用会话绑定的仓库，修复前端写死 ID 导致的血案
    conversation = db.get(Conversation, task.conversation_id)
    repo_id = payload.repository_id
    if conversation and conversation.repository_id:
        repo_id = conversation.repository_id
        
    repository = get_owned_repository(db, repo_id, current_user.id)
    code_change = await generate_code_change(db, task, repository)
    await event_service.publish_code_change_event(task.conversation_id, code_change)
    return code_change


@router.post("/{code_change_id}/accept", response_model=CodeChangeRead)
async def accept_code_change(
    code_change_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CodeChange:
    code_change = get_owned_code_change(db, code_change_id, current_user.id)
    task = get_owned_task(db, code_change.task_id, current_user.id)
    code_change_service.accept_code_change(code_change)
    db.commit()
    db.refresh(code_change)
    await event_service.publish_code_change_event(task.conversation_id, code_change, "code_change.accepted")
    return code_change


@router.post("/{code_change_id}/reject", response_model=CodeChangeRead)
async def reject_code_change(
    code_change_id: int,
    payload: CodeChangeReject,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CodeChange:
    code_change = get_owned_code_change(db, code_change_id, current_user.id)
    task = get_owned_task(db, code_change.task_id, current_user.id)
    code_change_service.reject_code_change(code_change, payload.reason)
    db.commit()
    db.refresh(code_change)
    await event_service.publish_code_change_event(task.conversation_id, code_change, "code_change.rejected")
    return code_change


@router.post("/{code_change_id}/revise", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def revise_code_change(
    code_change_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    code_change = get_owned_code_change(db, code_change_id, current_user.id)
    source_task = get_owned_task(db, code_change.task_id, current_user.id)
    task_service.ensure_user_task_capacity(db, current_user.id)
    revision_task = code_change_service.create_revision_task(db, code_change, source_task)
    await event_service.publish_task_event(revision_task, "task.created")

    if revision_task.agent.adapter_type == "langgraph":
        result = agent_tasks.run_orchestrator_task.delay(revision_task.id)
    else:
        result = agent_tasks.run_agent_task.delay(revision_task.id)

    revision_task.celery_task_id = result.id
    db.commit()
    db.refresh(revision_task)
    await event_service.publish_task_event(revision_task, "task.updated")
    return revision_task


@router.post("/{code_change_id}/review", response_model=CodeReviewRead, status_code=status.HTTP_201_CREATED)
async def review_code_change(
    code_change_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CodeReview:
    code_change = get_owned_code_change(db, code_change_id, current_user.id)
    task = get_owned_task(db, code_change.task_id, current_user.id)
    review = code_review_service.generate_code_review(db, code_change)
    await event_service.publish_code_review_event(task.conversation_id, review)
    return review


@router.get("/{code_change_id}/reviews", response_model=list[CodeReviewRead])
def list_code_change_reviews(
    code_change_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CodeReview]:
    code_change = get_owned_code_change(db, code_change_id, current_user.id)
    statement = (
        select(CodeReview)
        .where(CodeReview.code_change_id == code_change.id)
        .order_by(CodeReview.created_at.desc(), CodeReview.id.desc())
    )
    return list(db.scalars(statement))


@router.get("/{task_id}", response_model=list[CodeChangeRead])
def list_task_code_changes(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CodeChange]:
    task = get_owned_task(db, task_id, current_user.id)
    conversation_tasks = list(
        db.scalars(select(Task).where(Task.conversation_id == task.conversation_id))
    )
    descendant_task_ids = {task.id}
    changed = True
    while changed:
        changed = False
        for candidate in conversation_tasks:
            if candidate.parent_task_id in descendant_task_ids and candidate.id not in descendant_task_ids:
                descendant_task_ids.add(candidate.id)
                changed = True

    statement = (
        select(CodeChange)
        .where(CodeChange.task_id.in_(descendant_task_ids))
        .order_by(CodeChange.created_at.desc(), CodeChange.id.desc())
    )
    return list(db.scalars(statement))

