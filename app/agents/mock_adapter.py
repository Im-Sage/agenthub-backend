from app.agents.base import AgentAdapter, AgentRunRequest, AgentRunResult
from app.services.workspace_service import workspace_service, WorkspaceError

class MockAgentAdapter(AgentAdapter):
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        changed_files = []
        if request.repo_path:
            # 如果提供了代码仓库路径，模拟 Agent 在真实工作区修改代码
            try:
                target_file = f"agenthub_changes/task_{request.task_id}.md"
                content = (
                    f"# AgentHub Task {request.task_id}\n\n"
                    f"- 任务指令：{request.instruction}\n\n"
                    "这是 Mock Agent 在工作区中真实写入的文件。\n"
                )
                workspace_service.write_file(request.repo_path, target_file, content)
                changed_files.append(target_file)
            except WorkspaceError as e:
                return AgentRunResult(
                    status="failed",
                    summary=f"Mock Agent 写入文件失败：{e}",
                )

        return AgentRunResult(
            status="success",
            summary=f"Mock Agent 已处理任务：{request.instruction}",
            changed_files=changed_files,
            diff=None,
            logs="mock logs",
        )

