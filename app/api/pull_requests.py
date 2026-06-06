from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_code_change, get_owned_task
from app.db.session import get_db
from app.models.pull_request import PullRequest
from app.models.user import User
from app.schemas.pull_request import PullRequestCreate, PullRequestRead
from app.services import event_service
from app.services.repo_service import create_pull_request_from_code_change


router = APIRouter()


@router.post("", response_model=PullRequestRead, status_code=status.HTTP_201_CREATED)
async def create_pull_request(
    payload: PullRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PullRequest:
    code_change = get_owned_code_change(db, payload.code_change_id, current_user.id)
    pull_request = await create_pull_request_from_code_change(db, code_change, payload.title, payload.body)
    task = get_owned_task(db, pull_request.task_id, current_user.id)
    await event_service.publish_pull_request_event(task.conversation_id, pull_request)
    return pull_request


@router.get("/{task_id}", response_model=list[PullRequestRead])
def list_task_pull_requests(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PullRequest]:
    get_owned_task(db, task_id, current_user.id)
    statement = (
        select(PullRequest)
        .where(PullRequest.task_id == task_id)
        .order_by(PullRequest.created_at.desc(), PullRequest.id.desc())
    )
    return list(db.scalars(statement))

