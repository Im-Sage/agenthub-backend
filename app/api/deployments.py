from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_code_change, get_owned_task
from app.core.broadcaster import broadcaster
from app.db.session import get_db
from app.models.deployment import Deployment
from app.models.user import User
from app.schemas.deployment import DeploymentCreate, DeploymentEvent, DeploymentRead
from app.services import deployment_service


router = APIRouter()


@router.post("", response_model=DeploymentRead, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    payload: DeploymentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Deployment:
    code_change = get_owned_code_change(db, payload.code_change_id, current_user.id)
    deployment = deployment_service.create_local_deployment(db, code_change.id)
    task = get_owned_task(db, deployment.task_id, current_user.id)
    event = DeploymentEvent(data=DeploymentRead.model_validate(deployment))
    await broadcaster.publish(f"conv_{task.conversation_id}", jsonable_encoder(event))
    return deployment


@router.get("/{task_id}", response_model=list[DeploymentRead])
def list_task_deployments(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Deployment]:
    get_owned_task(db, task_id, current_user.id)
    statement = (
        select(Deployment)
        .where(Deployment.task_id == task_id)
        .order_by(Deployment.created_at.desc(), Deployment.id.desc())
    )
    return list(db.scalars(statement))

