import json
import logging
import shutil
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_owned_repository
from app.core.config import PROJECT_ROOT
from app.models.code_change import CodeChange
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.task import Task
from app.services import code_change_service
from app.services.workspace_service import workspace_service, WorkspaceError


WORKSPACE_ROOT = PROJECT_ROOT / "workspaces"
logger = logging.getLogger(__name__)


async def create_repository(
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
        workspace_path = await workspace_service.clone_repository(user_id, repository.id, repo_url)
    except WorkspaceError as e:
        db.delete(repository)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    repository.local_path = str(workspace_path)
    db.commit()
    db.refresh(repository)
    try:
        from app.workers.index_tasks import index_repository_task

        index_repository_task.delay(repository.id)
    except Exception:
        logger.exception(
            "repository_index_dispatch_failed repository_id=%s",
            repository.id,
        )
    return repository


def list_owned_repositories(db: Session, user_id: int) -> list[Repository]:
    statement = select(Repository).where(Repository.user_id == user_id).order_by(Repository.id.asc())
    return list(db.scalars(statement))


def generated_file_for_task(workspace_path: Path, task_id: int) -> Path:
    generated_dir = workspace_path / "agenthub_changes"
    generated_dir.mkdir(exist_ok=True)
    return generated_dir / f"task_{task_id}.md"


async def generate_code_change(
    db: Session,
    task: Task,
    repository: Repository,
    *,
    workspace_path: str | None = None,
    branch_name: str | None = None,
    base_commit_hash: str | None = None,
    result_commit_hash: str | None = None,
) -> CodeChange:
    worktree_values = (
        workspace_path,
        branch_name,
        base_commit_hash,
        result_commit_hash,
    )
    worktree_mode = all(value is not None for value in worktree_values)
    if any(value is not None for value in worktree_values) and not worktree_mode:
        raise ValueError(
            "workspace_path, branch_name, base_commit_hash, and "
            "result_commit_hash must be provided together"
        )
    branch_name = branch_name or f"agent-task-{task.id}"
    from app.services import task_service
    
    try:
        from git import Repo
        if worktree_mode:
            with Repo(workspace_path) as repo:
                repo.commit(base_commit_hash)
                repo.commit(result_commit_hash)
                revision_range = (
                    f"{base_commit_hash}..{result_commit_hash}"
                )
                changed_files = [
                    line
                    for line in repo.git.diff(
                        "--name-only",
                        revision_range,
                    ).splitlines()
                    if line
                ]
                diff_text = repo.git.diff(revision_range)
                commit_hash = result_commit_hash
        else:
            with Repo(repository.local_path) as repo:
                await task_service.broadcast_task_log(
                    task,
                    f"Preparing branch: {branch_name}",
                )
                repo.git.checkout("-B", branch_name)
                await task_service.broadcast_task_log(
                    task,
                    "Staging files for diff...",
                )
                repo.git.add(A=True)
            changed_files = workspace_service.get_changed_files(
                repository.local_path
            )
            diff_text = workspace_service.get_diff(repository.local_path)
            commit_hash = workspace_service.get_commit_hash(
                repository.local_path
            )
        await task_service.broadcast_task_log(task, f"Diff captured for {len(changed_files)} files.")
    except WorkspaceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Git operation failed: {e}")

    parent_code_change_id = None
    revision_index = 1
    if task.metadata_json:
        try:
            task_metadata = json.loads(task.metadata_json)
        except json.JSONDecodeError:
            task_metadata = {}
        source_code_change_id = task_metadata.get("source_code_change_id")
        if source_code_change_id:
            source_code_change = db.get(CodeChange, source_code_change_id)
            if source_code_change is not None:
                parent_code_change_id = source_code_change.id
                revision_index = (source_code_change.revision_index or 1) + 1

    code_change = CodeChange(
        task_id=task.id,
        repository_id=repository.id,
        parent_code_change_id=parent_code_change_id,
        revision_index=revision_index,
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


from app.services.github_service import github_service, GitHubError

async def create_pull_request_from_code_change(
    db: Session,
    code_change: CodeChange,
    title: str,
    body: str | None,
) -> PullRequest:
    code_change_service.require_accepted(code_change, "creating a pull request")

    repository = db.get(Repository, code_change.repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="仓库不存在")

    try:
        # 1. 直接提交变更（因为 generate 阶段已经写好文件并 add 过了，千万不要再 reset）
        from git import Repo, GitCommandError
        with Repo(repository.local_path) as repo:
            try:
                branch_commit = repo.commit(
                    code_change.branch_name
                ).hexsha
            except Exception:
                branch_commit = None
            already_committed = (
                bool(code_change.commit_hash)
                and branch_commit == code_change.commit_hash
            )
            if not already_committed:
                try:
                    repo.git.checkout(code_change.branch_name)
                except GitCommandError:
                    repo.git.checkout("-B", code_change.branch_name)

        commit_hash = (
            code_change.commit_hash
            if already_committed
            else await workspace_service.commit_changes(
                repository.local_path,
                title,
            )
        )
        
        # 2. 推送分支到远程仓库
        await workspace_service.push_branch(repository.local_path, code_change.branch_name)

        # 3. 调用 GitHub API 创建真实 PR
        pr_info = github_service.create_pull_request(
            repo_url=repository.repo_url,
            title=title,
            body=body or f"Auto-generated PR by AgentHub for task {code_change.task_id}",
            head_branch=code_change.branch_name,
            base_branch=repository.default_branch
        )

    except WorkspaceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except GitHubError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    code_change_service.mark_committed(code_change, commit_hash)
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
        pr_url=pr_info["html_url"],
        pr_number=pr_info.get("pr_number"),
        html_url=pr_info.get("html_url"),
        state=pr_info.get("state"),
        merged=bool(pr_info.get("merged", False)),
        base_branch=repository.default_branch,
        head_branch=code_change.branch_name,
        status="created",
    )
    db.add(pull_request)
    db.commit()
    db.refresh(pull_request)
    return pull_request



def reset_workspace_for_test(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
