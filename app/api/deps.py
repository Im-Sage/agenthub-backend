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


bearer_scheme = HTTPBearer()

# ... (get_current_user, get_user_from_token)

def get_owned_conversation(db: Session, conversation_id: int, user_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return conversation


def get_owned_task(db: Session, task_id: int, user_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    get_owned_conversation(db, task.conversation_id, user_id)
    return task


def get_owned_repository(db: Session, repository_id: int, user_id: int) -> Repository:
    repository = db.get(Repository, repository_id)
    if repository is None or repository.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="仓库不存在")
    return repository


def get_owned_code_change(db: Session, code_change_id: int, user_id: int) -> CodeChange:
    code_change = db.get(CodeChange, code_change_id)
    if code_change is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="代码变更不存在")

    get_owned_task(db, code_change.task_id, user_id)
    return code_change


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

