import json
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
from app.services.workspace_service import workspace_service, WorkspaceError


WORKSPACE_ROOT = PROJECT_ROOT / "workspaces"


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
    from app.services import task_service
    
    try:
        # 1. 确保在正确的分支（不执行 reset，保留 Agent 的修改）
        from git import Repo
        repo = Repo(repository.local_path)
        
        await task_service.broadcast_task_log(task, f"Preparing branch: {branch_name}")
        repo.git.checkout('-B', branch_name)
        
        # 将所有新文件和修改加入暂存区，确保后续的 Diff 和 Changed files 能捕捉到
        await task_service.broadcast_task_log(task, "Staging files for diff...")
        repo.git.add(A=True)

        # 2. 获取变更文件列表和差异
        changed_files = workspace_service.get_changed_files(repository.local_path)
        diff_text = workspace_service.get_diff(repository.local_path)
        commit_hash = workspace_service.get_commit_hash(repository.local_path)
        await task_service.broadcast_task_log(task, f"Diff captured for {len(changed_files)} files.")
    except WorkspaceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Git operation failed: {e}")

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


from app.services.github_service import github_service, GitHubError

async def create_pull_request_from_code_change(
    db: Session,
    code_change: CodeChange,
    title: str,
    body: str | None,
) -> PullRequest:
    repository = db.get(Repository, code_change.repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="仓库不存在")

    try:
        # 1. 直接提交变更（因为 generate 阶段已经写好文件并 add 过了，千万不要再 reset）
        from git import Repo, GitCommandError
        repo = Repo(repository.local_path)
        try:
            repo.git.checkout(code_change.branch_name)
        except GitCommandError:
            # 如果分支不存在，可能是被清理了，尝试创建并切换
            repo.git.checkout('-B', code_change.branch_name)
        
        # 由于这是同步上下文中的调用（例如从 run_agent_task），如果是 async 方法需要 await
        # 但如果是从 API 直接调用（如 pull_requests.py），也需要是 async。
        # 统一改为 async 并加上 task 记录（如果有）
        commit_hash = await workspace_service.commit_changes(repository.local_path, title)
        
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
        pr_url=pr_info["html_url"],
        status="created",
    )
    db.add(pull_request)
    db.commit()
    db.refresh(pull_request)
    return pull_request



def reset_workspace_for_test(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)

