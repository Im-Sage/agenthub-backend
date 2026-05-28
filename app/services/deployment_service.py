from html import escape
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models.code_change import CodeChange
from app.models.deployment import Deployment
from app.models.task import Task


PREVIEW_ROOT = PROJECT_ROOT / "previews"


def create_preview_deployment(
    db: Session,
    code_change: CodeChange,
    provider: str,
) -> Deployment:
    task = db.get(Task, code_change.task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    deployment = Deployment(
        task_id=code_change.task_id,
        code_change_id=code_change.id,
        provider=provider,
        preview_url="",
        status="success",
        logs="本地静态预览已生成。",
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)

    preview_dir = PREVIEW_ROOT / f"deployment-{deployment.id}"
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_file = preview_dir / "index.html"
    preview_file.write_text(
        build_preview_html(deployment, code_change, task),
        encoding="utf-8",
    )

    deployment.preview_url = f"/previews/deployment-{deployment.id}/index.html"
    db.commit()
    db.refresh(deployment)
    return deployment


def build_preview_html(deployment: Deployment, code_change: CodeChange, task: Task) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>AgentHub Preview #{deployment.id}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; color: #1f2937; }}
    pre {{ background: #f3f4f6; padding: 16px; overflow: auto; border-radius: 6px; }}
    .meta {{ color: #4b5563; }}
  </style>
</head>
<body>
  <h1>AgentHub 预览部署 #{deployment.id}</h1>
  <p class="meta">任务 ID：{task.id} ｜ 代码变更 ID：{code_change.id} ｜ 分支：{escape(code_change.branch_name)}</p>
  <h2>任务指令</h2>
  <p>{escape(task.instruction)}</p>
  <h2>变更文件</h2>
  <pre>{escape(code_change.changed_files)}</pre>
  <h2>Diff</h2>
  <pre>{escape(code_change.diff_text)}</pre>
</body>
</html>
"""

