from langchain_core.messages import HumanMessage
from app.agents.base import AgentAdapter, AgentRunRequest, AgentRunResult
from app.agents.graph.workflow import agent_graph


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
            "is_finished": False,
            "final_summary": None,
            "metadata_json": None
        }

        # 2. 执行图
        # 为了能让前端实时看到进度，我们可能需要使用 stream 模式，但先实现 invoke 跑通流程
        # 这里的 final_state 包含了整个执行过程中的状态变更，最终会有执行结果、错误信息、是否等待确认等状态标记
        final_state = await agent_graph.ainvoke(initial_state)

        # 3. 返回结果
        # changed_files 汇总
        all_changed_files = []
        for result in final_state.get("execution_results", []):
            all_changed_files.extend(result.get("files", []))

        status = "awaiting_confirmation" if final_state.get("awaiting_confirmation") else "success"

        return AgentRunResult(
            status=status,
            summary=final_state.get("final_summary", "任务处理完成"),
            changed_files=list(set(all_changed_files)),
            logs=f"LangGraph executed {len(final_state.get('plan', []))} steps."
        )
