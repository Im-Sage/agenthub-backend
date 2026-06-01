import json
import shutil
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models.code_change import CodeChange
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.task import Task
from app.services.workspace_service import workspace_service, WorkspaceError


WORKSPACE_ROOT = PROJECT_ROOT / "workspaces"


def create_repository(
    db: Session,
    user_id: int,
    name: str,
    repo_url: str,
    default_branch: str,
) -> Repository:
    repository = Repository(
        user_id=user_id,
        name=name,
        repo_url=repo_url,
        local_path="",
        default_branch=default_branch,
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)

    try:
        workspace_path = workspace_service.clone_repository(repository.id, repo_url)
    except WorkspaceError as e:
        db.delete(repository)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    repository.local_path = str(workspace_path)
    db.commit()
    db.refresh(repository)
    return repository


def get_owned_repository(db: Session, repository_id: int, user_id: int) -> Repository:
    repository = db.get(Repository, repository_id)
    if repository is None or repository.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="仓库不存在")
    return repository


def list_owned_repositories(db: Session, user_id: int) -> list[Repository]:
    statement = select(Repository).where(Repository.user_id == user_id).order_by(Repository.id.asc())
    return list(db.scalars(statement))


def generated_file_for_task(workspace_path: Path, task_id: int) -> Path:
    generated_dir = workspace_path / "agenthub_changes"
    generated_dir.mkdir(exist_ok=True)
    return generated_dir / f"task_{task_id}.md"


async def generate_code_change(db: Session, task: Task, repository: Repository) -> CodeChange:
    branch_name = f"agent-task-{task.id}"
    
    try:
        workspace_service.prepare_branch(repository.local_path, repository.default_branch, branch_name)
    except WorkspaceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # 让 Agent 真实修改代码
    from app.models.agent import Agent
    from app.agents.base import AgentRunRequest
    from app.services.task_service import get_adapter

    agent = db.get(Agent, task.agent_id)
    if agent:
        adapter = get_adapter(agent)
        request = AgentRunRequest(
            task_id=task.id,
            conversation_id=task.conversation_id,
            instruction=task.instruction,
            repo_path=repository.local_path,
            branch_name=branch_name,
            context={"system_prompt": agent.system_prompt or ""}
        )
        await adapter.run(request)

    try:
        changed_files = workspace_service.get_changed_files(repository.local_path)
        diff_text = workspace_service.get_diff(repository.local_path)
        commit_hash = workspace_service.get_commit_hash(repository.local_path)
    except WorkspaceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    code_change = CodeChange(
        task_id=task.id,
        repository_id=repository.id,
        repo_url=repository.repo_url,
        branch_name=branch_name,
        commit_hash=commit_hash,
        changed_files=json.dumps(changed_files, ensure_ascii=False),
        diff_text=diff_text,
        status="generated",
    )
    db.add(code_change)
    db.commit()
    db.refresh(code_change)
    return code_change


def create_pull_request_from_code_change(
    db: Session,
    code_change: CodeChange,
    title: str,
    body: str | None,
) -> PullRequest:
    repository = db.get(Repository, code_change.repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="仓库不存在")

    try:
        workspace_service.prepare_branch(repository.local_path, repository.default_branch, code_change.branch_name)
        commit_hash = workspace_service.commit_changes(repository.local_path, title)
    except WorkspaceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    code_change.commit_hash = commit_hash
    code_change.status = "committed"

    db.commit()
    db.refresh(code_change)

    pull_request = PullRequest(
        code_change_id=code_change.id,
        task_id=code_change.task_id,
        repository_id=code_change.repository_id,
        branch_name=code_change.branch_name,
        commit_hash=commit_hash,
        title=title,
        body=body,
        pr_url=f"agenthub://repos/{repository.id}/pulls/{code_change.branch_name}",
        status="created",
    )
    db.add(pull_request)
    db.commit()
    db.refresh(pull_request)
    return pull_request


def reset_workspace_for_test(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)

