import httpx

from app.agents.base import AgentAdapter, AgentRunRequest, AgentRunResult
from app.core.config import settings


class QwenAgentAdapter(AgentAdapter):
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        if not settings.aliyun_api_key:
            raise RuntimeError("未配置 ALIYUN_API_KEY，无法调用阿里通义千问。")

        system_prompt = request.context.get(
            "system_prompt",
            "你是 AgentHub 中的真实 LLM Agent。请用中文回答，保持简洁、结构清晰，并围绕用户任务给出可执行建议。",
        )
        payload = {
            "model": settings.aliyun_model,
            "messages": [
                {"role": "system", "content": system_prompt},
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
        return AgentRunResult(
            status="success",
            summary=content,
            logs=f"provider=aliyun model={settings.aliyun_model}",
        )

