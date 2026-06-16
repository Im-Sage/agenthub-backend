from typing import Callable, Awaitable
from app.core.config import settings
from app.mcp.client import MCPToolClient
from app.tools.base import ToolCallRequest, ToolCallResult, ToolDefinition, ToolRiskLevel


ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolCallResult]]


class ToolRegistry:
    def __init__(self):
        # 存储工具的元数据（名称、描述、参数定义、风险等级）。
        self._definitions: dict[str, ToolDefinition] = {}
        # 存储工具的实际处理函数，供本地调用使用。
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    async def call(self, request: ToolCallRequest) -> ToolCallResult:
        definition = self._definitions.get(request.name)
        if definition is None:
            return ToolCallResult(success=False, error=f"Unknown tool: {request.name}")

        if definition.risk_level == ToolRiskLevel.HIGH and not request.require_confirmation:
            return ToolCallResult(
                success=False,
                error=f"Tool {request.name} requires user confirmation before execution.",
            )

        result = await self._execute(request)
        self._record_audit(request, result, definition)
        self._broadcast_task_log(request, result)
        return result

    async def _execute(self, request: ToolCallRequest) -> ToolCallResult:
        mode = self._tool_mode()

        if mode == "mcp":
            return await self._call_mcp(request)

        if mode == "hybrid" and request.name not in self._handlers:
            return await self._call_mcp(request)

        # 默认使用本地处理，除非在混合模式下没有注册本地处理函数
        return await self._call_local(request)

    async def _call_local(self, request: ToolCallRequest) -> ToolCallResult:
        # 仅调用在 _handlers 中注册的本地 Python 函数，如果没有注册则返回错误
        handler = self._handlers.get(request.name)
        if handler is None:
            return ToolCallResult(success=False, error=f"No local handler registered for tool: {request.name}")

        try:
            return await handler(request)
        except Exception as exc:
            return ToolCallResult(success=False, error=str(exc))

    async def _call_mcp(self, request: ToolCallRequest) -> ToolCallResult:
        if not settings.mcp_workspace_server_url:
            return ToolCallResult(
                success=False,
                error="MCP tool mode is enabled, but mcp_workspace_server_url is not configured.",
            )

        client = MCPToolClient(settings.mcp_workspace_server_url, token=settings.mcp_internal_token)
        mcp_name = self._to_mcp_tool_name(request.name)

        try:
            result = await client.call_tool(mcp_name, request.arguments)
        except Exception as exc:
            return ToolCallResult(success=False, error=f"MCP tool call failed: {exc}")

        return ToolCallResult(
            success=result.get("success", False),
            content=result.get("content", ""),
            structured_content=result.get("structured_content", {}),
            error=None if result.get("success", False) else result.get("content") or "MCP tool returned an error.",
        )

    def _tool_mode(self) -> str:
        if not settings.mcp_enabled:
            return "local"

        mode = (settings.mcp_tool_mode or "local").lower()
        if mode not in {"local", "mcp", "hybrid"}:
            return "local"
        return mode

    @staticmethod
    def _to_mcp_tool_name(name: str) -> str:
        return name.replace(".", "_")

    @staticmethod
    def _record_audit(request: ToolCallRequest, result: ToolCallResult, definition: ToolDefinition) -> None:
        from app.tools.audit import record_tool_call
        record_tool_call(request, result, definition.risk_level.value)

    @staticmethod
    def _broadcast_task_log(request: ToolCallRequest, result: ToolCallResult) -> None:
        if request.task_id:
            from app.services import task_service
            from app.db.session import SessionLocal
            from app.models.task import Task
            import asyncio
            
            db = SessionLocal()
            try:
                task = db.get(Task, request.task_id)
                if task:
                    log_msg = f"Tool '{request.name}' executed."
                    if not result.success:
                        log_msg += f" Error: {result.error}"
                    
                    # 避免在异步上下文中直接调用需要 await 的服务，使用任务发射或者确保正确环境
                    asyncio.create_task(task_service.broadcast_task_log(task, log_msg))
            finally:
                db.close()


tool_registry = ToolRegistry()
