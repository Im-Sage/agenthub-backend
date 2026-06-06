import httpx
from typing import Any

from app.agents.base import AgentAdapter, AgentRunRequest, AgentRunResult
from app.core.config import settings
from app.core.logging import get_logger
from app.services.workspace_service import WorkspaceError, workspace_service


logger = get_logger("agent.qwen")


FILE_OPERATION_PROMPT = """
When changing files, use these exact file operation markers:
[FILE: relative/path]
```language
content
```
[DELETE: relative/path]
[RENAME: old/relative/path -> new/relative/path]
Do not output code blocks without a [FILE: ] marker.
""".strip()


class QwenAgentAdapter(AgentAdapter):
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        if not settings.aliyun_api_key:
            raise RuntimeError("ALIYUN_API_KEY is not configured.")

        base_system_prompt = request.context.get(
            "system_prompt",
            "You are an AI engineer in AgentHub. Answer concisely and professionally.",
        )

        if request.repo_path:
            base_system_prompt += f"\n\nCurrent workspace: {request.repo_path}\n{FILE_OPERATION_PROMPT}"

        payload = {
            "model": settings.aliyun_model,
            "messages": [
                {"role": "system", "content": base_system_prompt},
                {"role": "user", "content": request.instruction},
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.aliyun_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{settings.aliyun_base_url.rstrip('/')}/chat/completions"

        async with httpx.AsyncClient(timeout=settings.aliyun_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        changed_files: list[str] = []
        if request.repo_path:
            changed_files = await self._apply_file_changes(request.repo_path, content, request.task)

        return AgentRunResult(
            status="success",
            summary=content,
            changed_files=changed_files,
            logs=f"provider=aliyun model={settings.aliyun_model} files_changed={len(changed_files)}",
        )

    async def _apply_file_changes(self, repo_path: str, content: str, task: Any | None = None) -> list[str]:
        try:
            return await workspace_service.apply_operations_from_text(repo_path, content, task=task)
        except WorkspaceError as exc:
            logger.exception("apply_file_operations_failed error=%s", exc)
            return []
