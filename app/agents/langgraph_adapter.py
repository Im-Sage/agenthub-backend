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
            # 第一次执行时，直接传入 initial_state，图会根据状态流转到各个节点
            # 其中运行到approval_node()时，interrupt会被触发，状态会被保存下来到SQLite，等待人工确认
            final_state = await graph.ainvoke(
                initial_state,
                config=config,
            )

        return self._to_run_result(final_state)

    """
    resume 方法用于在人工确认后继续执行任务
    1. 接收 task_id 和 resume_value（人工确认的结果）
    2. 使用 graph_config 获取图的配置
    3. 打开状态图，调用 ainvoke 方法继续执行，传入 Command(resume=resume_value) 作为输入
    4. 获取最终状态 final_state
    5. 调用 _to_run_result 将 final_state 转换为 AgentRunResult
    6. 返回 AgentRunResult
    """
    async def resume(
        self,
        task_id: int,
        resume_value: dict, # 人工确认的结果，可能包含 approval_status、additional_instructions 等信息
    ) -> AgentRunResult:

        # 必须生成与第一次执行时相同的thread_id
        config = graph_config(task_id) # 获取图的配置，即 task_id 对应的状态图配置

        # 继续执行图，传入 Command(resume=resume_value) 作为输入
        async with open_agent_graph() as graph:
            final_state = await graph.ainvoke(
                Command(resume=resume_value),
                config=config,
            )

        return self._to_run_result(final_state)

    """
    将最终状态转换为 AgentRunResult
    1. 如果 final_state 中有 __interrupt__ 或 awaiting_confirmation 标记，说明需要人工确认，返回 status 为 awaiting_confirmation
    2. 否则，返回 status 为 success，并汇总 changed_files、summary、logs 等信息
    3. changed_files 需要去重，使用 dict.fromkeys 保留顺序
    4. summary 优先使用 final_state 中的 final_summary，如果没有则使用默认值 "任务处理完成"
    5. logs 中记录执行了多少步，使用 final_state 中的 plan 长度来计算
    6. 返回 AgentRunResult 对象
    """
    @staticmethod
    def _to_run_result(final_state: dict) -> AgentRunResult:
        # 如果 final_state 中有 __interrupt__ 或 awaiting_confirmation 标记，说明需要人工确认
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
