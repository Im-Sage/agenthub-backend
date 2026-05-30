from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.user import TokenRead, UserCreate, UserLogin


def register_user(db: Session, payload: UserCreate) -> User:
    existing_user = db.scalar(
        select(User).where(or_(User.username == payload.username, User.email == payload.email))
    )
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名或邮箱已存在")

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login_user(db: Session, payload: UserLogin) -> TokenRead:
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    token = create_access_token(subject=str(user.id))
    return TokenRead(access_token=token, user=user)
