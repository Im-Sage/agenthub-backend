import json
import shutil
import subprocess
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models.code_change import CodeChange
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

# prepare_repository_workspace 函数的设计目的是为了确保在处理仓库相关操作之前，
# 能够在本地准备好一个有效的 Git 工作区。它首先检查指定的工作区路径是否已经存在，
# 如果存在则验证它是否是一个 Git 仓库；如果不存在，则尝试从给定的 repo_url 克隆仓库到该路径。
# 这个函数的设计考虑了以下几个方面：
# 1. 工作区准备：确保每个仓库都有一个对应的本地工作区，方便后续的 Git 操作和代码变更生成。
# 2. 本地路径支持：如果 repo_url 是一个本地路径，函数会直接使用该路径进行克隆，避免不必要的网络操作，提高效率。
# 3. 错误处理：在克隆或验证过程中，如果发生任何错误，函数会抛出 HTTPException，确保调用者能够正确处理这些错误，并提供清晰的错误信息。
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


def clean_generated_file(workspace_path: Path, task_id: int) -> Path:
    generated_dir = workspace_path / "agenthub_changes"
    generated_dir.mkdir(exist_ok=True)
    return generated_dir / f"task_{task_id}.md"


def generate_code_change(db: Session, task: Task, repository: Repository) -> CodeChange:
    workspace_path = Path(repository.local_path)
    ensure_git_repo(workspace_path)

    branch_name = f"agent-task-{task.id}"
    run_git(["checkout", repository.default_branch], cwd=workspace_path)
    run_git(["checkout", "-B", branch_name], cwd=workspace_path)

    generated_file = clean_generated_file(workspace_path, task.id)
    generated_file.write_text(
        "\n".join(
            [
                f"# AgentHub Task {task.id}",
                "",
                f"- 任务状态：{task.status}",
                f"- 任务指令：{task.instruction}",
                f"- 结果摘要：{task.result_summary or '暂无'}",
                "",
                "这个文件由第五阶段的 Mock Diff 流程生成，用于演示 Git 工作区和 diff 保存。",
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


def reset_workspace_for_test(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
