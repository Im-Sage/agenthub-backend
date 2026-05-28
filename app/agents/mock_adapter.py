from app.agents.base import AgentAdapter, AgentRunRequest, AgentRunResult


class MockAgentAdapter(AgentAdapter):
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(
            status="success",
            summary=f"Mock Agent 已处理任务：{request.instruction}",
            changed_files=[],
            diff=None,
            logs="mock logs",
        )

