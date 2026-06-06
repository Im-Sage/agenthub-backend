import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models.code_change import CodeChange
from app.models.deployment import Deployment
from app.models.repository import Repository
from app.schemas.enums import DeploymentStatus
from app.services import code_change_service


PREVIEW_ROOT = PROJECT_ROOT / "previews"
BUILD_TIMEOUT_SECONDS = 180
INSTALL_TIMEOUT_SECONDS = 240
STATIC_EXCLUDES = {
    ".git",
    ".env",
    ".venv",
    "__pycache__",
    "agenthub_changes",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".cache",
}


def _clean_preview_dir(preview_path: Path) -> None:
    if preview_path.exists():
        shutil.rmtree(preview_path)
    preview_path.mkdir(parents=True, exist_ok=True)


def _copy_preview_source(source_dir: Path, preview_dir: Path) -> None:
    for item in source_dir.iterdir():
        if item.name in STATIC_EXCLUDES:
            continue

        target = preview_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns(*STATIC_EXCLUDES))
        else:
            shutil.copy2(item, target)


def _detect_package_manager(workspace_path: Path) -> tuple[str, list[str], list[str]]:
    if (workspace_path / "pnpm-lock.yaml").exists():
        return "pnpm", ["pnpm", "install", "--frozen-lockfile"], ["pnpm", "run", "build"]
    if (workspace_path / "yarn.lock").exists():
        return "yarn", ["yarn", "install", "--frozen-lockfile"], ["yarn", "build"]
    if (workspace_path / "package-lock.json").exists():
        return "npm", ["npm", "ci"], ["npm", "run", "build"]
    return "npm", ["npm", "install"], ["npm", "run", "build"]


def _has_build_script(package_json_path: Path) -> bool:
    try:
        package_data = json.loads(package_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    scripts = package_data.get("scripts")
    return isinstance(scripts, dict) and bool(scripts.get("build"))


def _run_command(command: list[str], cwd: Path, timeout: int) -> str:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed with exit code {result.returncode}\n{output}")
    return output


def _find_build_output(workspace_path: Path) -> Path | None:
    for candidate in ["dist", "build", "out"]:
        output_path = workspace_path / candidate
        if output_path.exists() and output_path.is_dir():
            return output_path
    return None


def _prepare_preview_source(workspace_path: Path) -> tuple[Path, list[str]]:
    build_logs = [f"Preparing local preview from {workspace_path}"]
    package_json = workspace_path / "package.json"

    if not package_json.exists():
        build_logs.append("No package.json found. Using static file preview.")
        return workspace_path, build_logs

    if not _has_build_script(package_json):
        build_logs.append("package.json found but no build script exists. Using static file preview.")
        return workspace_path, build_logs

    package_manager, install_command, build_command = _detect_package_manager(workspace_path)
    build_logs.append(f"Detected {package_manager} project.")

    if not (workspace_path / "node_modules").exists():
        build_logs.append(f"Installing dependencies: {' '.join(install_command)}")
        install_output = _run_command(install_command, workspace_path, INSTALL_TIMEOUT_SECONDS)
        if install_output:
            build_logs.append(install_output)
    else:
        build_logs.append("node_modules already exists. Skipping dependency installation.")

    build_logs.append(f"Running build: {' '.join(build_command)}")
    build_output = _run_command(build_command, workspace_path, BUILD_TIMEOUT_SECONDS)
    if build_output:
        build_logs.append(build_output)

    build_output_path = _find_build_output(workspace_path)
    if build_output_path is None:
        build_logs.append("Build completed but no dist/build/out directory was found. Using workspace files.")
        return workspace_path, build_logs

    build_logs.append(f"Using build output: {build_output_path}")
    return build_output_path, build_logs


def create_local_deployment(db: Session, code_change_id: int) -> Deployment:
    code_change = db.get(CodeChange, code_change_id)
    if code_change is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CodeChange not found")
    code_change_service.require_accepted(code_change, "creating a deployment")

    repository = db.get(Repository, code_change.repository_id)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    workspace_path = Path(repository.local_path)
    if not workspace_path.exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path does not exist")

    deployment = Deployment(
        task_id=code_change.task_id,
        code_change_id=code_change.id,
        provider="local",
        status=DeploymentStatus.PENDING,
        started_at=datetime.utcnow(),
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)

    try:
        deployment.status = DeploymentStatus.RUNNING
        db.commit()

        preview_dir = PREVIEW_ROOT / f"user_{repository.user_id}" / f"repo_{repository.id}" / f"task_{code_change.task_id}"
        _clean_preview_dir(preview_dir)

        preview_source, build_logs = _prepare_preview_source(workspace_path)
        build_logs.append(f"Copying preview files to {preview_dir}")
        _copy_preview_source(preview_source, preview_dir)
        build_logs.append("Preview files copied successfully.")

        deployment.status = DeploymentStatus.SUCCESS
        deployment.preview_url = f"/previews/user_{repository.user_id}/repo_{repository.id}/task_{code_change.task_id}/"
        deployment.build_logs = "\n".join(build_logs)
        deployment.deploy_logs = f"Local preview served from {preview_dir}"
        deployment.finished_at = datetime.utcnow()
    except Exception as exc:
        deployment.status = DeploymentStatus.FAILED
        deployment.deploy_logs = str(exc)
        deployment.finished_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Local deployment failed: {exc}")

    db.commit()
    db.refresh(deployment)
    return deployment
