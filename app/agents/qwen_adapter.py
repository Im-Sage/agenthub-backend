from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import (
    AgentAdapter,
    AgentRunRequest,
    AgentRunResult,
)
from app.agents.llm_factory import get_chat_llm
from app.agents.tool_calling import run_tool_calling_agent
from app.core.config import settings


class QwenAgentAdapter(AgentAdapter):
    async def run(
        self,
        request: AgentRunRequest,
    ) -> AgentRunResult:
        if not settings.aliyun_api_key:
            raise RuntimeError("ALIYUN_API_KEY is not configured.")

        agent_code = str(
            request.context.get("agent_code") or "qwen"
        )

        system_prompt = request.context.get(
            "system_prompt",
            "You are an AI engineer in AgentHub.",
        )

        if request.repo_path:
            system_prompt += (
                "\n\nYou have access to repository workspace tools. "
                "Use tools to inspect and modify the repository. "
                "Read relevant files before overwriting them. "
                "Do not emit custom [FILE:], [DELETE:], or [RENAME:] "
                "markers for normal workspace operations."
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=request.instruction),
        ]

        previous_error = request.context.get("previous_error")
        if previous_error:
            messages.append(
                HumanMessage(
                    content=(
                        "Previous execution failed with this error:\n"
                        f"{previous_error}\n"
                        "Inspect the current workspace and fix it."
                    )
                )
            )

        # 调用 run_tool_calling_agent 来执行智能体的操作
        result = await run_tool_calling_agent(
            llm=get_chat_llm(),
            messages=messages,
            agent_code=agent_code,
            repo_path=request.repo_path,
            repository_id=request.repository_id,
            user_id=request.user_id,
            task_id=request.task_id,
            conversation_id=request.conversation_id,
        )

        return AgentRunResult(
            status="success",
            summary=result.summary,
            changed_files=result.changed_files,
            logs=(
                f"provider=aliyun "
                f"model={settings.aliyun_model} "
                f"files_changed={len(result.changed_files)} "
                f"legacy_fallback={result.used_legacy_fallback}"
            ),
        )
