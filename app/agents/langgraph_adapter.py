from langchain_core.messages import HumanMessage
from langgraph.types import Command

from app.agents.base import AgentAdapter, AgentRunRequest, AgentRunResult
from app.agents.graph.runtime import graph_config, open_agent_graph


class LangGraphOrchestratorAdapter(AgentAdapter):
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        # 1. 准备初始状态
        initial_state = {
            "messages": [HumanMessage(content=request.instruction)],
            "task_id": request.task_id,
            "conversation_id": request.conversation_id,
            "repo_path": request.repo_path,
            "plan": [],
            "current_step_index": 0,
            "current_agent": None,
            "current_instruction": None,
            "execution_results": [],
            "errors": [],
            "awaiting_confirmation": False,
            "approval_status": None,
            "is_finished": False,
            "final_summary": None,
            "metadata_json": None
        }

        # 2. 执行图
        # 为了能让前端实时看到进度，我们可能需要使用 stream 模式，但先实现 invoke 跑通流程
        # 这里的 final_state 包含了整个执行过程中的状态变更，最终会有执行结果、错误信息、是否等待确认等状态标记
        config = graph_config(request.task_id)
        async with open_agent_graph() as graph:
            final_state = await graph.ainvoke(
                initial_state,
                config=config,
            )

        return self._to_run_result(final_state)

    async def resume(
        self,
        task_id: int,
        resume_value: dict,
    ) -> AgentRunResult:
        config = graph_config(task_id)
        async with open_agent_graph() as graph:
            final_state = await graph.ainvoke(
                Command(resume=resume_value),
                config=config,
            )

        return self._to_run_result(final_state)

    @staticmethod
    def _to_run_result(final_state: dict) -> AgentRunResult:
        if final_state.get("__interrupt__") or final_state.get(
            "awaiting_confirmation"
        ):
            return AgentRunResult(
                status="awaiting_confirmation",
                summary=(
                    "Orchestrator plan generated and is awaiting confirmation."
                ),
                changed_files=[],
                logs="LangGraph interrupted for human approval.",
            )

        all_changed_files = []
        for result in final_state.get("execution_results", []):
            all_changed_files.extend(result.get("files", []))

        return AgentRunResult(
            status="success",
            summary=final_state.get("final_summary") or "任务处理完成",
            changed_files=list(dict.fromkeys(all_changed_files)),
            logs=f"LangGraph executed {len(final_state.get('plan', []))} steps."
        )
