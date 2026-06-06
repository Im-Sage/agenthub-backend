from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.models.conversation import Conversation
from app.models.task import Task
from app.models.repository import Repository
from app.models.code_change import CodeChange


from app.services.task_service import (
    get_owned_conversation,
    get_owned_task,
    get_owned_repository,
    get_owned_code_change,
)


bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录状态无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload.get("sub", ""))
    except (InvalidTokenError, ValueError):
        raise credentials_exception

    user = db.get(User, user_id)
    if user is None:
        raise credentials_exception
    return user


def get_user_from_token(db: Session, token: str) -> User | None:
    """给 WebSocket 使用的轻量鉴权函数，因为浏览器 WebSocket 不方便设置 Authorization 头。"""
    try:
        payload = decode_access_token(token)
        user_id = int(payload.get("sub", ""))
    except (InvalidTokenError, ValueError):
        return None

    return db.get(User, user_id)

