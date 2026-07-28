from typing import Any


class MCPClientError(RuntimeError):
    pass


class MCPToolClient:
    def __init__(self, server_url: str, token: str | None = None):
        self.server_url = server_url
        self.token = token

    async def list_tools(self) -> list[dict[str, Any]]:
        session = await self._open_session()
        async with session as client_session:
            result = await client_session.list_tools()
            tools = getattr(result, "tools", result)
            return [self._tool_to_dict(tool) for tool in tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        session = await self._open_session()
        async with session as client_session:
            result = await client_session.call_tool(name, arguments)
            return self._tool_result_to_dict(result)

    async def _open_session(self):
        if not self.token:
            raise MCPClientError(
                "MCP internal token is not configured."
            )
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise MCPClientError("MCP SDK is not installed. Run `pip install -r requirements.txt`.") from exc

        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        transport = streamablehttp_client(self.server_url, headers=headers or None)

        class _SessionContext:
            async def __aenter__(context_self):
                context_self.transport_context = transport
                read_stream, write_stream, *_ = await context_self.transport_context.__aenter__()
                context_self.session_context = ClientSession(read_stream, write_stream)
                context_self.session = await context_self.session_context.__aenter__()
                await context_self.session.initialize()
                return context_self.session

            async def __aexit__(context_self, exc_type, exc, tb):
                await context_self.session_context.__aexit__(exc_type, exc, tb)
                await context_self.transport_context.__aexit__(exc_type, exc, tb)

        return _SessionContext()

    @staticmethod
    def _tool_to_dict(tool: Any) -> dict[str, Any]:
        if hasattr(tool, "model_dump"):
            return tool.model_dump()
        if isinstance(tool, dict):
            return tool
        return {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
            "inputSchema": getattr(tool, "inputSchema", {}),
        }

    @staticmethod
    def _tool_result_to_dict(result: Any) -> dict[str, Any]:
        if hasattr(result, "model_dump"):
            data = result.model_dump()
        elif isinstance(result, dict):
            data = result
        else:
            data = {
                "content": getattr(result, "content", []),
                "structuredContent": getattr(result, "structuredContent", None),
                "isError": getattr(result, "isError", False),
            }

        content = data.get("content") or []
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
            else:
                text = getattr(item, "text", None)
            if text:
                text_parts.append(text)

        structured_content = data.get("structuredContent") or data.get("structured_content") or {}
        return {
            "success": not bool(data.get("isError") or data.get("is_error")),
            "content": "\n".join(text_parts),
            "structured_content": structured_content,
            "raw": data,
        }
