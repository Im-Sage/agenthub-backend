from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_repository
from app.db.session import get_db
from app.models.repository import Repository
from app.models.user import User
from app.schemas.repository import RepositoryCreate, RepositoryRead
from app.services.repo_service import create_repository, list_owned_repositories


router = APIRouter()


@router.post("", response_model=RepositoryRead, status_code=status.HTTP_201_CREATED)
async def bind_repository(
    payload: RepositoryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Repository:
    return await create_repository(
        db=db,
        user_id=current_user.id,
        name=payload.name,
        repo_url=payload.repo_url,
        default_branch=payload.default_branch,
    )


@router.get("", response_model=list[RepositoryRead])
def list_repositories(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Repository]:
    return list_owned_repositories(db, current_user.id)


@router.get("/{repository_id}", response_model=RepositoryRead)
def get_repository(
    repository_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Repository:
    return get_owned_repository(db, repository_id, current_user.id)

