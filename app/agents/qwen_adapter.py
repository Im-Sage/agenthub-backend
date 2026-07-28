from app.agents.base import (
    AgentAdapter,
    AgentRunRequest,
    AgentRunResult,
)
from app.agents.context import ContextAssembler, ContextSource
from app.agents.llm_factory import get_chat_llm
from app.agents.tool_calling import run_tool_calling_agent
from app.core.config import settings


context_assembler = ContextAssembler()


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

        previous_results = list(
            request.context.get("previous_results") or []
        )
        supplemental_result = {
            key: request.context[key]
            for key in (
                "changed_files",
                "git_diff_summary",
                "verification_results",
                "plan_step_index",
                "parent_task_id",
            )
            if request.context.get(key) not in (None, [], "")
        }
        if supplemental_result:
            previous_results.append(supplemental_result)
        previous_errors = list(
            request.context.get("previous_errors") or []
        )
        previous_error = request.context.get("previous_error")
        if previous_error and previous_error not in previous_errors:
            previous_errors.append(previous_error)
        assembled = await context_assembler.assemble(
            system_prompt=system_prompt,
            instruction=request.instruction,
            conversation_id=request.conversation_id,
            repository_id=request.repository_id,
            user_id=request.user_id,
            previous_results=previous_results,
            previous_errors=previous_errors,
        )

        # 调用 run_tool_calling_agent 来执行智能体的操作
        result = await run_tool_calling_agent(
            llm=get_chat_llm(),
            messages=assembled.messages,
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
                f"legacy_fallback={result.used_legacy_fallback} "
                f"context_tokens={assembled.estimated_tokens} "
                f"retrieval_chunks={sum(block.source == ContextSource.RETRIEVAL for block in assembled.blocks)} "
                f"truncated_blocks={len(assembled.truncated_blocks)}"
            ),
        )
