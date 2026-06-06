from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limit import rate_limiter
from app.db.session import get_db
from app.schemas.user import TokenRead, UserCreate, UserLogin, UserRead
from app.services import auth_service


router = APIRouter()


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    return auth_service.register_user(db, payload)


@router.post("/login", response_model=TokenRead)
def login(payload: UserLogin, request: Request, db: Session = Depends(get_db)):
    client_host = request.client.host if request.client else "unknown"
    rate_limiter.hit(
        f"login:{client_host}:{payload.username}",
        limit=settings.login_rate_limit_count,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    return auth_service.login_user(db, payload)
