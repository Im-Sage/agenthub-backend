import time
from typing import Callable, Awaitable
from app.core.logging import (
    get_logger,
    log_agent_event,
    safe_error_summary,
)
from app.core.config import settings
from app.mcp.client import MCPToolClient
from app.tools.base import (
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
    ToolRiskLevel,
    ToolSource,
)


ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolCallResult]]
logger = get_logger("tools")

# 整个系统中，ToolRegistry 类是一个核心组件，用于管理和调用各种工具。
# 它提供了注册工具、列出工具、获取工具定义以及调用工具的功能。
# 通过 ToolRegistry，开发者可以轻松地将新的工具集成到系统中，并确保在调用工具时能够正确处理参数、执行逻辑以及记录审计信息。
class ToolRegistry:
    def __init__(self):
        # 存储工具的元数据（名称、描述、参数定义、风险等级）。
        self._definitions: dict[str, ToolDefinition] = {}
        # 存储工具的实际处理函数，供本地调用使用。
        self._handlers: dict[str, ToolHandler] = {}
        # 存储 canonical 工具名到远程 MCP 精确路由的映射。
        self._remote_routes: dict[str, ToolDefinition] = {}

    """
    当开发者注册一个新工具时，调用 register 方法将工具的定义和处理函数添加到注册表中。
    这样，工具就可以被系统识别，并在需要时调用其处理函数。
    1. definition: ToolDefinition 对象，包含工具的名称、描述、参数定义和风险等级。
    2. handler: ToolHandler 异步函数，用于处理工具调用请求，返回 ToolCallResult。
    3. 将 definition 存储在 _definitions 字典中，以工具名称为键。
    4. 将 handler 存储在 _handlers 字典中，以工具名称为键。
    5. 这样，系统就可以根据工具名称查找其定义和处理函数，并在调用时执行相应的逻辑。
    """
    def register(
        self,
        definition: ToolDefinition,
        handler: ToolHandler | None = None,
        *,
        replace: bool = False,
    ) -> None:
        if definition.name in self._definitions and not replace:
            if (
                self._definitions[definition.name] == definition
                and self._handlers.get(definition.name) is handler
            ):
                return
            raise ValueError(f"Tool {definition.name} is already registered")
        self._definitions[definition.name] = definition
        if handler is None:
            self._handlers.pop(definition.name, None)
        else:
            self._handlers[definition.name] = handler

    def register_remote(self, definition: ToolDefinition) -> None:
        if definition.source != ToolSource.MCP:
            raise ValueError("Remote tool definition must use source=mcp")
        if not definition.server_id:
            raise ValueError("Remote tool definition requires server_id")
        if not definition.remote_name:
            raise ValueError("Remote tool definition requires remote_name")

        current_route = self._remote_routes.get(definition.name)
        if (
            current_route is not None
            and current_route.server_id != definition.server_id
        ):
            raise ValueError(
                f"Tool {definition.name} is already routed by MCP server "
                f"{current_route.server_id}"
            )

        self._remote_routes[definition.name] = definition
        current_definition = self._definitions.get(definition.name)
        if (
            current_definition is None
            or current_definition.source == ToolSource.MCP
        ):
            self._definitions[definition.name] = definition

    def unregister_remote_source(self, server_id: str) -> int:
        names = [
            name
            for name, route in self._remote_routes.items()
            if route.server_id == server_id
        ]
        for name in names:
            self._remote_routes.pop(name, None)
            definition = self._definitions.get(name)
            if (
                definition is not None
                and definition.source == ToolSource.MCP
                and definition.server_id == server_id
            ):
                self._definitions.pop(name, None)
                self._handlers.pop(name, None)
        return len(names)

    def remote_route(self, name: str) -> ToolDefinition | None:
        return self._remote_routes.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    async def call(self, request: ToolCallRequest) -> ToolCallResult:
        started = time.perf_counter()
        definition = self._definitions.get(request.name)
        if definition is None:
            return ToolCallResult(success=False, error=f"Unknown tool: {request.name}")

        if definition.risk_level == ToolRiskLevel.HIGH and not request.require_confirmation:
            return ToolCallResult(
                success=False,
                error=f"Tool {request.name} requires user confirmation before execution.",
            )

        result = await self._execute(request)
        log_agent_event(
            logger,
            "mcp.tool_called",
            task_id=request.task_id,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            repository_id=request.repository_id,
            tool_name=request.name,
            duration_ms=int((time.perf_counter() - started) * 1000),
            success=result.success,
            error_type=None if result.success else "ToolCallError",
            error_summary=safe_error_summary(result.error),
        )
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

    """
    _call_local 方法用于调用在 _handlers 中注册的本地 Python 函数。
    1. 根据请求的工具名称，从 _handlers 字典中查找对应的处理函数。
    2. 如果没有找到处理函数，返回一个失败的 ToolCallResult，提示没有注册本地处理函数。
    3. 如果找到了处理函数，调用它并传入请求对象。
    4. 如果处理函数执行成功，返回其结果。
    5. 如果处理函数执行过程中抛出异常，捕获异常并返回一个失败的 ToolCallResult，包含异常信息。
    6. 这样，系统可以在本地环境中执行工具调用，并处理可能的错误情况，确保调用过程的稳定性和可靠性。
    """
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
        if not settings.mcp_internal_token:
            return ToolCallResult(
                success=False,
                error="MCP tool mode is enabled, but mcp_internal_token is not configured.",
            )
        if request.repository_id is None or request.user_id is None:
            return ToolCallResult(
                success=False,
                error="MCP workspace tools require trusted repository_id and user_id.",
            )

        client = MCPToolClient(settings.mcp_workspace_server_url, token=settings.mcp_internal_token)
        route = self.remote_route(request.name)
        mcp_name = (
            route.remote_name
            if route is not None and route.remote_name
            else self._to_mcp_tool_name(request.name)
        )
        arguments = {
            key: value
            for key, value in request.arguments.items()
            if key not in {"local_path", "repository_id", "user_id"}
        }
        arguments.update(
            repository_id=request.repository_id,
            user_id=request.user_id,
        )

        try:
            result = await client.call_tool(mcp_name, arguments)
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
                        log_msg += (
                            f" Error: {safe_error_summary(result.error)}"
                        )
                    
                    # 避免在异步上下文中直接调用需要 await 的服务，使用任务发射或者确保正确环境
                    asyncio.create_task(task_service.broadcast_task_log(task, log_msg))
            except Exception as exc:
                log_agent_event(
                    logger,
                    "tool.broadcast_failed",
                    task_id=request.task_id,
                    conversation_id=request.conversation_id,
                    user_id=request.user_id,
                    repository_id=request.repository_id,
                    tool_name=request.name,
                    success=False,
                    error_type=type(exc).__name__,
                )
            finally:
                db.close()


tool_registry = ToolRegistry()
