import json
import shutil
import subprocess
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models.code_change import CodeChange
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.task import Task


WORKSPACE_ROOT = PROJECT_ROOT / "workspaces"


def run_git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Git 命令执行失败"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return result.stdout.strip()


def ensure_git_repo(path: Path) -> None:
    if not (path / ".git").exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="目标路径不是 Git 仓库")


def prepare_repository_workspace(repo_id: int, repo_url: str) -> Path:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    workspace_path = WORKSPACE_ROOT / f"repo-{repo_id}"
    if workspace_path.exists():
        ensure_git_repo(workspace_path)
        return workspace_path

    source_path = Path(repo_url)
    if source_path.exists():
        ensure_git_repo(source_path)
        run_git(["clone", str(source_path), str(workspace_path)])
    else:
        run_git(["clone", repo_url, str(workspace_path)])

    return workspace_path


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
        workspace_path = prepare_repository_workspace(repository.id, repo_url)
    except Exception:
        db.delete(repository)
        db.commit()
        raise

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


def generate_code_change(db: Session, task: Task, repository: Repository) -> CodeChange:
    workspace_path = Path(repository.local_path)
    ensure_git_repo(workspace_path)

    branch_name = f"agent-task-{task.id}"
    run_git(["checkout", repository.default_branch], cwd=workspace_path)
    run_git(["checkout", "-B", branch_name], cwd=workspace_path)

    generated_file = generated_file_for_task(workspace_path, task.id)
    generated_file.write_text(
        "\n".join(
            [
                f"# AgentHub Task {task.id}",
                "",
                f"- 任务状态：{task.status}",
                f"- 任务指令：{task.instruction}",
                f"- 结果摘要：{task.result_summary or '暂无'}",
                "",
                "这个文件由 AgentHub 的 Diff 流程生成，用于演示 Git 工作区和代码变更保存。",
                "",
            ]
        ),
        encoding="utf-8",
    )

    relative_generated_file = generated_file.relative_to(workspace_path).as_posix()
    run_git(["add", "-N", relative_generated_file], cwd=workspace_path)
    changed_files = run_git(["diff", "--name-only"], cwd=workspace_path).splitlines()
    diff_text = run_git(["diff"], cwd=workspace_path)
    commit_hash = run_git(["rev-parse", "HEAD"], cwd=workspace_path)

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

    workspace_path = Path(repository.local_path)
    ensure_git_repo(workspace_path)
    run_git(["checkout", code_change.branch_name], cwd=workspace_path)
    run_git(["config", "user.email", "agenthub@example.com"], cwd=workspace_path)
    run_git(["config", "user.name", "AgentHub"], cwd=workspace_path)

    changed_files = json.loads(code_change.changed_files)
    if not changed_files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可提交的代码变更")

    run_git(["add", *changed_files], cwd=workspace_path)
    status_output = run_git(["status", "--porcelain"], cwd=workspace_path)
    if not status_output:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="工作区没有可提交的变更")

    run_git(["commit", "-m", title], cwd=workspace_path)
    commit_hash = run_git(["rev-parse", "HEAD"], cwd=workspace_path)

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

