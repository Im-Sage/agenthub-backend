import pytest
from app.tools.base import ToolCallRequest
import app.tools.registry as registry_module
from app.tools.registry import tool_registry

@pytest.mark.anyio
async def test_unknown_tool():
    request = ToolCallRequest(name="unknown_tool", arguments={})
    result = await tool_registry.call(request)
    assert result.success is False
    assert "Unknown tool" in result.error

@pytest.mark.anyio
async def test_high_risk_tool_without_confirmation():
    # Assuming workspace.delete_file is high risk
    request = ToolCallRequest(
        name="workspace.delete_file",
        arguments={"local_path": "/tmp", "target_file": "test.txt"},
        require_confirmation=False
    )
    result = await tool_registry.call(request)
    assert result.success is False
    assert "requires user confirmation" in result.error


@pytest.mark.anyio
async def test_mcp_mode_requires_workspace_server_url(monkeypatch):
    monkeypatch.setattr(registry_module.settings, "mcp_enabled", True)
    monkeypatch.setattr(registry_module.settings, "mcp_tool_mode", "mcp")
    monkeypatch.setattr(registry_module.settings, "mcp_workspace_server_url", None)

    request = ToolCallRequest(
        name="workspace.get_diff",
        arguments={
            "local_path": "C:/attacker",
            "repository_id": 999,
            "user_id": 999,
        },
        repository_id=42,
        user_id=7,
    )
    result = await tool_registry.call(request)

    assert result.success is False
    assert "mcp_workspace_server_url is not configured" in result.error


@pytest.mark.anyio
async def test_mcp_mode_calls_mcp_client(monkeypatch):
    calls = []

    class FakeMCPToolClient:
        def __init__(self, server_url, token=None):
            self.server_url = server_url
            self.token = token

        async def call_tool(self, name, arguments):
            calls.append((self.server_url, self.token, name, arguments))
            return {
                "success": True,
                "content": "Diff retrieved successfully",
                "structured_content": {"diff": "fake diff"},
            }

    monkeypatch.setattr(registry_module.settings, "mcp_enabled", True)
    monkeypatch.setattr(registry_module.settings, "mcp_tool_mode", "mcp")
    monkeypatch.setattr(registry_module.settings, "mcp_workspace_server_url", "http://127.0.0.1:9000/mcp")
    monkeypatch.setattr(registry_module.settings, "mcp_internal_token", "test-token")
    monkeypatch.setattr(registry_module, "MCPToolClient", FakeMCPToolClient)

    request = ToolCallRequest(
        name="workspace.get_diff",
        arguments={
            "local_path": "C:/attacker",
            "repository_id": 999,
            "user_id": 999,
        },
        repository_id=42,
        user_id=7,
    )
    result = await tool_registry.call(request)

    assert result.success is True
    assert result.structured_content == {"diff": "fake diff"}
    assert calls == [
        (
            "http://127.0.0.1:9000/mcp",
            "test-token",
            "workspace_get_diff",
            {"repository_id": 42, "user_id": 7},
        )
    ]
