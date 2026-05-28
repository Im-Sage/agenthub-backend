from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.agent import Agent
from app.models.user import User
from app.schemas.agent import AgentRead


router = APIRouter()


@router.get("", response_model=list[AgentRead])
def list_agents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Agent]:
    statement = select(Agent).where(Agent.enabled.is_(True)).order_by(Agent.id.asc())
    return list(db.scalars(statement))

