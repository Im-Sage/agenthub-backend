from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_code_change, get_owned_task, get_owned_repository
from app.core.websocket_manager import websocket_manager
from app.db.session import get_db
from app.models.code_change import CodeChange
from app.models.conversation import Conversation
from app.models.user import User
from app.schemas.code_change import CodeChangeEvent, CodeChangeGenerate, CodeChangeRead
from app.services.repo_service import generate_code_change


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
    event = CodeChangeEvent(data=CodeChangeRead.model_validate(code_change))
    await websocket_manager.broadcast_json(task.conversation_id, jsonable_encoder(event))
    return code_change


@router.get("/{task_id}", response_model=list[CodeChangeRead])
def list_task_code_changes(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CodeChange]:
    get_owned_task(db, task_id, current_user.id)
    statement = (
        select(CodeChange)
        .where(CodeChange.task_id == task_id)
        .order_by(CodeChange.created_at.desc(), CodeChange.id.desc())
    )
    return list(db.scalars(statement))

