from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.code_change import CodeChange
from app.models.task import Task
from app.schemas.enums import CodeChangeStatus
from app.schemas.enums import TaskStatus


def _status_value(value: str) -> str:
    return value.value if hasattr(value, "value") else str(value)


def require_status(code_change: CodeChange, expected: CodeChangeStatus, action: str) -> None:
    if _status_value(code_change.status) != expected.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CodeChange must be {expected.value} before {action}. Current status: {code_change.status}.",
        )


def accept_code_change(code_change: CodeChange) -> None:
    require_status(code_change, CodeChangeStatus.GENERATED, "accepting")
    code_change.status = CodeChangeStatus.ACCEPTED
    code_change.reject_reason = None


def reject_code_change(code_change: CodeChange, reason: str) -> None:
    require_status(code_change, CodeChangeStatus.GENERATED, "rejecting")
    code_change.status = CodeChangeStatus.REJECTED
    code_change.reject_reason = reason


def require_accepted(code_change: CodeChange, action: str) -> None:
    require_status(code_change, CodeChangeStatus.ACCEPTED, action)


def mark_committed(code_change: CodeChange, commit_hash: str) -> None:
    require_accepted(code_change, "committing")
    code_change.commit_hash = commit_hash
    code_change.status = CodeChangeStatus.COMMITTED


def create_revision_task(db: Session, code_change: CodeChange, source_task: Task) -> Task:
    require_status(code_change, CodeChangeStatus.REJECTED, "creating a revision task")
    if not code_change.reject_reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rejected CodeChange must have a reject reason before creating a revision task.",
        )

    instruction = (
        "Revise the previous code change according to the user's rejection feedback.\n\n"
        f"Original task:\n{source_task.instruction}\n\n"
        f"Rejection reason:\n{code_change.reject_reason}\n\n"
        "Use the existing workspace state and produce corrected file operations. "
        "If files need to be changed, use [FILE:], [DELETE:], or [RENAME:] markers."
    )

    revision_task = Task(
        conversation_id=source_task.conversation_id,
        parent_task_id=source_task.id,
        agent_id=source_task.agent_id,
        status=TaskStatus.PENDING,
        task_type="revision",
        instruction=instruction,
        metadata_json=f'{{"source_code_change_id": {code_change.id}}}',
    )
    db.add(revision_task)
    db.commit()
    db.refresh(revision_task)
    return revision_task
