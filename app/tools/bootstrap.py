import asyncio
from threading import Lock

from app.core.config import settings
from app.mcp.discovery import MCPDiscoveryReport, MCPDiscoveryService
from app.tools import register_builtin_tools


_local_tools_registered = False
_registration_lock = Lock()


def register_local_tools_once() -> None:
    global _local_tools_registered
    if _local_tools_registered:
        return
    with _registration_lock:
        if _local_tools_registered:
            return
        register_builtin_tools()
        _local_tools_registered = True


async def initialize_tool_registry() -> MCPDiscoveryReport | None:
    register_local_tools_once()
    if (
        not settings.mcp_enabled
        or settings.mcp_tool_mode.lower() == "local"
        or not settings.mcp_dynamic_discovery_enabled
    ):
        return None
    return await MCPDiscoveryService().refresh()


def initialize_tool_registry_sync() -> MCPDiscoveryReport | None:
    return asyncio.run(initialize_tool_registry())
