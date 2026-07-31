import pytest

import app.tools.registry as registry_module
from app.tools.base import (
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
    ToolRiskLevel,
    ToolSource,
)
from app.tools.registry import ToolRegistry


def _definition(
    name: str,
    *,
    source: ToolSource = ToolSource.LOCAL,
    server_id: str | None = None,
    remote_name: str | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        risk_level=ToolRiskLevel.LOW,
        input_schema={"type": "object", "properties": {}},
        source=source,
        server_id=server_id,
        remote_name=remote_name,
    )


async def _local_handler(request: ToolCallRequest) -> ToolCallResult:
    return ToolCallResult(success=True, content=f"local:{request.name}")


def test_remote_route_can_attach_without_replacing_local_handler():
    registry = ToolRegistry()
    local = _definition("workspace.read_file")
    remote = _definition(
        "workspace.read_file",
        source=ToolSource.MCP,
        server_id="workspace",
        remote_name="workspace_read_file_v2",
    )

    registry.register(local, _local_handler)
    registry.register_remote(remote)

    assert registry.get_tool(local.name) == local
    assert registry.remote_route(local.name) == remote
    assert registry._handlers[local.name] is _local_handler


def test_unknown_remote_tool_can_be_registered_without_local_handler():
    registry = ToolRegistry()
    remote = _definition(
        "mcp.workspace.search",
        source=ToolSource.MCP,
        server_id="workspace",
        remote_name="search",
    )

    registry.register_remote(remote)

    assert registry.get_tool(remote.name) == remote
    assert registry.remote_route(remote.name) == remote
    assert remote.name not in registry._handlers


def test_unregister_remote_source_removes_stale_routes_but_keeps_local_tools():
    registry = ToolRegistry()
    local = _definition("workspace.read_file")
    attached = _definition(
        local.name,
        source=ToolSource.MCP,
        server_id="workspace",
        remote_name="workspace_read_file",
    )
    remote_only = _definition(
        "mcp.workspace.search",
        source=ToolSource.MCP,
        server_id="workspace",
        remote_name="search",
    )
    registry.register(local, _local_handler)
    registry.register_remote(attached)
    registry.register_remote(remote_only)

    removed = registry.unregister_remote_source("workspace")

    assert removed == 2
    assert registry.remote_route(local.name) is None
    assert registry.get_tool(local.name) == local
    assert registry.get_tool(remote_only.name) is None


def test_refreshing_same_server_is_idempotent_and_other_server_collision_fails():
    registry = ToolRegistry()
    first = _definition(
        "mcp.workspace.search",
        source=ToolSource.MCP,
        server_id="workspace",
        remote_name="search_v1",
    )
    refreshed = first.model_copy(update={"remote_name": "search_v2"})
    collision = first.model_copy(
        update={"server_id": "other", "remote_name": "other_search"}
    )

    registry.register_remote(first)
    registry.register_remote(refreshed)

    assert registry.remote_route(first.name) == refreshed
    with pytest.raises(ValueError, match="already routed by MCP server workspace"):
        registry.register_remote(collision)


@pytest.mark.anyio
async def test_hybrid_prefers_local_and_mcp_uses_exact_remote_name(monkeypatch):
    calls = []

    class FakeMCPToolClient:
        def __init__(self, server_url, token=None):
            self.server_url = server_url
            self.token = token

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return {"success": True, "content": f"remote:{name}"}

    registry = ToolRegistry()
    local = _definition("workspace.read_file")
    remote = _definition(
        local.name,
        source=ToolSource.MCP,
        server_id="workspace",
        remote_name="exact.remote-name",
    )
    registry.register(local, _local_handler)
    registry.register_remote(remote)
    monkeypatch.setattr(registry_module.settings, "mcp_enabled", True)
    monkeypatch.setattr(
        registry_module.settings,
        "mcp_workspace_server_url",
        "http://127.0.0.1:9000/mcp",
    )
    monkeypatch.setattr(registry_module.settings, "mcp_internal_token", "token")
    monkeypatch.setattr(registry_module, "MCPToolClient", FakeMCPToolClient)

    request = ToolCallRequest(
        name=local.name,
        repository_id=42,
        user_id=7,
    )
    monkeypatch.setattr(registry_module.settings, "mcp_tool_mode", "hybrid")
    hybrid_result = await registry._execute(request)
    monkeypatch.setattr(registry_module.settings, "mcp_tool_mode", "mcp")
    mcp_result = await registry._execute(request)

    assert hybrid_result.content == "local:workspace.read_file"
    assert mcp_result.content == "remote:exact.remote-name"
    assert calls == [
        (
            "exact.remote-name",
            {"repository_id": 42, "user_id": 7},
        )
    ]


def test_register_requires_explicit_replace_for_existing_local_tool():
    registry = ToolRegistry()
    first = _definition("workspace.read_file")
    replacement = first.model_copy(update={"description": "replacement"})

    registry.register(first, _local_handler)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(replacement, _local_handler)
    registry.register(replacement, _local_handler, replace=True)
    assert registry.get_tool(first.name) == replacement
