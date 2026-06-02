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
    
    # 注意：这里我们不再调用 adapter.run(request)
    # 因为 Agent 在 run_agent_task 异步任务中已经完成了文件写入
    # 我们这里只负责捕捉 Git 状态
    
    try:
        # 1. 确保在正确的分支（不执行 reset，保留 Agent 的修改）
        from git import Repo
        repo = Repo(repository.local_path)
        repo.git.checkout('-B', branch_name)
        
        # 将所有新文件和修改加入暂存区，确保后续的 Diff 和 Changed files 能捕捉到
        repo.git.add(A=True)

        # 2. 获取变更文件列表和差异
        changed_files = workspace_service.get_changed_files(repository.local_path)
        diff_text = workspace_service.get_diff(repository.local_path)
        commit_hash = workspace_service.get_commit_hash(repository.local_path)
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
        # 1. 直接提交变更（因为 generate 阶段已经写好文件并 add 过了，千万不要再 reset）
        from git import Repo, GitCommandError
        repo = Repo(repository.local_path)
        try:
            repo.git.checkout(code_change.branch_name)
        except GitCommandError:
            # 如果分支不存在，可能是被清理了，尝试创建并切换
            repo.git.checkout('-B', code_change.branch_name)
        
        commit_hash = workspace_service.commit_changes(repository.local_path, title)
        
        # 2. 推送分支到远程仓库
        workspace_service.push_branch(repository.local_path, code_change.branch_name)

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

