import os
import shutil
from pathlib import Path
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models.code_change import CodeChange
from app.models.deployment import Deployment
from app.models.repository import Repository
from app.schemas.enums import DeploymentStatus


PREVIEW_ROOT = PROJECT_ROOT / "previews"


def _clean_preview_dir(preview_path: Path):
    if preview_path.exists():
        shutil.rmtree(preview_path)
    preview_path.mkdir(parents=True, exist_ok=True)


def create_local_deployment(db: Session, code_change_id: int) -> Deployment:
    """
    创建一个本地预览部署。
    目前针对静态 HTML/JS/CSS 项目，将工作区代码复制到专门的 preview 目录下供 Nginx/FastAPI 访问。
    """
    code_change = db.get(CodeChange, code_change_id)
    if code_change is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CodeChange not found")

    repository = db.get(Repository, code_change.repository_id)
    workspace_path = Path(repository.local_path)

    if not workspace_path.exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace path does not exist")

    # 1. 创建 Deployment 记录 (Pending 状态)
    deployment = Deployment(
        task_id=code_change.task_id,
        code_change_id=code_change.id,
        provider="local",
        status=DeploymentStatus.PENDING,
        started_at=datetime.utcnow()
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)

    try:
        deployment.status = DeploymentStatus.RUNNING
        db.commit()

        # 2. 准备预览目录
        # 路径规划: /previews/repo_{repo_id}/task_{task_id}/
        preview_dir = PREVIEW_ROOT / f"repo_{repository.id}" / f"task_{code_change.task_id}"
        _clean_preview_dir(preview_dir)

        # 3. 模拟“构建”过程（目前简单粗暴地将工作区文件复制到预览目录）
        # 这里排除了 .git 目录等敏感信息
        build_logs = ["Starting local build...", f"Copying files from {workspace_path} to {preview_dir}"]
        
        for item in os.listdir(workspace_path):
            if item in [".git", "agenthub_changes", ".env"]:
                continue
            s = workspace_path / item
            d = preview_dir / item
            if s.is_dir():
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        
        build_logs.append("Files copied successfully.")
        
        # 4. 生成可访问的 URL (由 app/main.py 的 StaticFiles 托管)
        # 例如: http://127.0.0.1:8000/previews/repo_1/task_106/index.html
        # 注意：这里我们默认入口是根目录，实际上前端可以在卡片上提供不同的文件链接
        preview_url = f"/previews/repo_{repository.id}/task_{code_change.task_id}/"

        # 5. 更新状态为成功
        deployment.status = DeploymentStatus.SUCCESS
        deployment.preview_url = preview_url
        deployment.build_logs = "\n".join(build_logs)
        deployment.finished_at = datetime.utcnow()

    except Exception as e:
        # 记录部署失败状态
        deployment.status = DeploymentStatus.FAILED
        deployment.deploy_logs = str(e)
        deployment.finished_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Local deployment failed: {e}")

    db.commit()
    db.refresh(deployment)
    return deployment
