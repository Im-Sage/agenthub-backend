import pytest

from app.core.config import Settings
from app.mcp.discovery import (
    MCPDiscoveryService,
    canonical_registry_name,
)
from app.tools.base import ToolRiskLevel
from app.tools.registry import ToolRegistry


class FakeClient:
    def __init__(self, tools=None, error=None):
        self.tools = list(tools or [])
        self.error = error
        self.calls = 0

    async def list_tools(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.tools


def _settings(**updates):
    values = {
        "mcp_enabled": True,
        "mcp_tool_mode": "hybrid",
        "mcp_workspace_server_url": "http://127.0.0.1:9000/mcp",
        "mcp_internal_token": "secret-token",
        "mcp_dynamic_discovery_enabled": True,
        "mcp_dynamic_server_id": "workspace",
        "mcp_dynamic_namespace": "mcp.workspace",
        "mcp_dynamic_fail_closed": False,
        "mcp_dynamic_allowlist": "workspace_read_file,workspace_write_file",
        "mcp_dynamic_denylist": "workspace_delete_file",
        "mcp_dynamic_medium_risk_tools": "workspace_write_file",
    }
    values.update(updates)
    return Settings(**values)


def _tool(name, description="description", schema=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": schema
        or {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }


def test_canonical_registry_name_preserves_workspace_compatibility():
    settings = _settings(mcp_dynamic_namespace="mcp.custom")

    assert canonical_registry_name("workspace_read_file", settings) == (
        "workspace.read_file"
    )
    assert canonical_registry_name("search", settings) == "mcp.custom.search"


@pytest.mark.anyio
async def test_refresh_validates_filters_and_sanitizes_discovered_tools():
    registry = ToolRegistry()
    trusted_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "local_path": {"type": "string"},
            "repository_id": {"type": "integer"},
            "user_id": {"type": "integer"},
            "worktree_path": {"type": "string"},
        },
        "required": [
            "path",
            "local_path",
            "repository_id",
            "user_id",
            "worktree_path",
        ],
    }
    client = FakeClient(
        [
            _tool("workspace_read_file", schema=trusted_schema),
            _tool("workspace_write_file"),
            _tool("safe_search"),
            _tool("workspace_delete_file"),
            _tool("run_shell"),
            _tool("1invalid"),
            _tool("missing_description", description=""),
            _tool("bad_schema", schema={"type": "array"}),
        ]
    )
    service = MCPDiscoveryService(
        registry=registry,
        client=client,
        settings_obj=_settings(),
    )

    report = await service.refresh()

    assert report.discovered == 8
    assert report.registered == 3
    assert report.updated == 0
    assert report.removed == 0
    assert report.denied == ("workspace_delete_file", "run_shell")
    assert report.invalid == (
        "1invalid",
        "missing_description",
        "bad_schema",
    )
    assert report.error is None

    read_tool = registry.get_tool("workspace.read_file")
    assert read_tool is not None
    assert read_tool.remote_name == "workspace_read_file"
    assert read_tool.risk_level == ToolRiskLevel.LOW
    assert read_tool.input_schema == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    assert (
        registry.get_tool("workspace.write_file").risk_level
        == ToolRiskLevel.MEDIUM
    )
    assert registry.get_tool("mcp.workspace.safe_search") is not None


@pytest.mark.anyio
async def test_refresh_is_idempotent_and_removes_stale_remote_tools():
    registry = ToolRegistry()
    client = FakeClient([_tool("first"), _tool("second")])
    service = MCPDiscoveryService(
        registry=registry,
        client=client,
        settings_obj=_settings(),
    )
    first = await service.refresh()
    client.tools = [
        _tool("first", description="updated"),
        _tool("third"),
    ]

    second = await service.refresh()
    third = await service.refresh()

    assert (first.registered, first.updated, first.removed) == (2, 0, 0)
    assert (second.registered, second.updated, second.removed) == (1, 1, 1)
    assert (third.registered, third.updated, third.removed) == (0, 0, 0)
    assert registry.get_tool("mcp.workspace.second") is None
    assert registry.get_tool("mcp.workspace.first").description == "updated"
    assert registry.get_tool("mcp.workspace.third") is not None


@pytest.mark.anyio
async def test_local_mode_skips_remote_discovery():
    registry = ToolRegistry()
    client = FakeClient([_tool("never_called")])
    service = MCPDiscoveryService(
        registry=registry,
        client=client,
        settings_obj=_settings(mcp_tool_mode="local"),
    )

    report = await service.refresh()

    assert client.calls == 0
    assert report.discovered == report.registered == report.removed == 0
    assert registry.list_tools() == []


@pytest.mark.anyio
async def test_hybrid_failure_reports_error_and_preserves_existing_routes():
    registry = ToolRegistry()
    client = FakeClient([_tool("existing")])
    service = MCPDiscoveryService(
        registry=registry,
        client=client,
        settings_obj=_settings(),
    )
    await service.refresh()
    client.error = RuntimeError("server unavailable")

    report = await service.refresh()

    assert report.error == "server unavailable"
    assert registry.get_tool("mcp.workspace.existing") is not None


@pytest.mark.anyio
async def test_strict_mcp_failure_is_raised():
    service = MCPDiscoveryService(
        registry=ToolRegistry(),
        client=FakeClient(error=RuntimeError("server unavailable")),
        settings_obj=_settings(
            mcp_tool_mode="mcp",
            mcp_dynamic_fail_closed=True,
        ),
    )

    with pytest.raises(RuntimeError, match="server unavailable"):
        await service.refresh()
