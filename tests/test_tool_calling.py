import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.tool_calling import (
    build_model_tools,
    contains_legacy_file_markers,
    model_tool_name,
    run_tool_calling_agent,
)
from app.core.config import Settings
from app.tools.base import (
    ToolCallResult,
    ToolDefinition,
    ToolRiskLevel,
)
from app.tools.registry import tool_registry


def _definition(
    name: str,
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Tool definition for {name}",
        risk_level=risk_level,
        input_schema={
            "type": "object",
            "properties": {
                "local_path": {"type": "string"},
                "target_file": {"type": "string"},
            },
            "required": ["local_path", "target_file"],
        },
    )


@pytest.fixture
def workspace_definitions(monkeypatch):
    definitions = [
        _definition("workspace.read_file"),
        _definition("workspace.list_files"),
        _definition("workspace.search_code"),
        _definition("workspace.write_file", ToolRiskLevel.MEDIUM),
        _definition("workspace.rename_file", ToolRiskLevel.MEDIUM),
        _definition("workspace.delete_file", ToolRiskLevel.HIGH),
        _definition("workspace.get_diff"),
        _definition("workspace.get_changed_files"),
    ]
    monkeypatch.setattr(tool_registry, "list_tools", lambda: definitions)
    return definitions


def _model_tool_names(tools: list[dict]) -> set[str]:
    return {tool["function"]["name"] for tool in tools}


class FakeToolCallingLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bound_tools = None
        self.received_messages = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        self.received_messages.append(list(messages))
        return self.responses.pop(0)


def test_model_tool_name_replaces_dots_with_underscores():
    assert model_tool_name("workspace.write_file") == "workspace_write_file"


def test_no_workspace_exposes_no_workspace_tools(workspace_definitions):
    tools, reverse_map = build_model_tools("backend", has_workspace=False)

    assert tools == []
    assert reverse_map == {}


def test_backend_exposes_read_write_and_rename_tools(workspace_definitions):
    tools, reverse_map = build_model_tools("backend", has_workspace=True)
    names = _model_tool_names(tools)

    assert "workspace_read_file" in names
    assert "workspace_write_file" in names
    assert "workspace_rename_file" in names
    assert reverse_map["workspace_write_file"] == "workspace.write_file"


def test_model_schema_hides_local_path_without_mutating_registry_definition(
    workspace_definitions,
):
    tools, _ = build_model_tools("backend", has_workspace=True)
    write_tool = next(
        tool
        for tool in tools
        if tool["function"]["name"] == "workspace_write_file"
    )
    parameters = write_tool["function"]["parameters"]
    original_write_definition = next(
        definition
        for definition in workspace_definitions
        if definition.name == "workspace.write_file"
    )

    assert "local_path" not in parameters["properties"]
    assert "local_path" not in parameters.get("required", [])
    assert "local_path" in original_write_definition.input_schema["properties"]
    assert "local_path" in original_write_definition.input_schema["required"]


def test_high_risk_delete_is_not_exposed(workspace_definitions):
    tools, reverse_map = build_model_tools("backend", has_workspace=True)

    assert "workspace_delete_file" not in _model_tool_names(tools)
    assert "workspace_delete_file" not in reverse_map


def test_reviewer_cannot_write_or_rename(workspace_definitions):
    tools, _ = build_model_tools("reviewer", has_workspace=True)
    names = _model_tool_names(tools)

    assert "workspace_read_file" in names
    assert "workspace_get_diff" in names
    assert "workspace_write_file" not in names
    assert "workspace_rename_file" not in names


def test_tool_calling_settings_have_safe_defaults():
    assert Settings.model_fields["agent_tool_max_rounds"].default == 8
    assert (
        Settings.model_fields[
            "agent_legacy_file_protocol_fallback"
        ].default
        is False
    )


@pytest.mark.anyio
async def test_native_tool_call_round_trip_injects_trusted_workspace(
    monkeypatch,
    workspace_definitions,
):
    llm = FakeToolCallingLlm(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_write_file",
                        "args": {
                            "target_file": "app/main.py",
                            "content": "print('ok')",
                            "local_path": "/malicious/path",
                        },
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Implemented app/main.py"),
        ]
    )
    received_requests = []

    async def fake_registry_call(request):
        received_requests.append(request)
        return ToolCallResult(
            success=True,
            content="File written: app/main.py",
            structured_content={"changed_files": ["app/main.py"]},
        )

    monkeypatch.setattr(tool_registry, "call", fake_registry_call)

    result = await run_tool_calling_agent(
        llm=llm,
        messages=[HumanMessage(content="Create the application entry point")],
        agent_code="backend",
        repo_path="/trusted/workspace",
        task_id=11,
        conversation_id=22,
        max_rounds=2,
    )

    assert llm.bound_tools
    assert len(received_requests) == 1
    request = received_requests[0]
    assert request.name == "workspace.write_file"
    assert request.task_id == 11
    assert request.conversation_id == 22
    assert request.arguments["local_path"] == "/trusted/workspace"
    assert len(llm.received_messages) == 2
    second_round_tool_message = llm.received_messages[1][-1]
    assert isinstance(second_round_tool_message, ToolMessage)
    assert second_round_tool_message.tool_call_id == "call-1"
    assert json.loads(second_round_tool_message.content)["success"] is True
    assert result.summary == "Implemented app/main.py"
    assert result.changed_files == ["app/main.py"]
    assert result.used_legacy_fallback is False


@pytest.mark.anyio
async def test_unknown_model_tool_becomes_failed_tool_message(
    monkeypatch,
    workspace_definitions,
):
    llm = FakeToolCallingLlm(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_run_shell",
                        "args": {"command": "dangerous"},
                        "id": "call-unknown",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The requested tool is unavailable."),
        ]
    )

    async def forbidden_registry_call(request):
        raise AssertionError(f"Unexpected tool execution: {request.name}")

    monkeypatch.setattr(tool_registry, "call", forbidden_registry_call)

    result = await run_tool_calling_agent(
        llm=llm,
        messages=[HumanMessage(content="Run a shell command")],
        agent_code="backend",
        repo_path="/trusted/workspace",
        task_id=11,
        conversation_id=22,
        max_rounds=2,
    )

    tool_message = llm.received_messages[1][-1]
    payload = json.loads(tool_message.content)
    assert isinstance(tool_message, ToolMessage)
    assert payload == {
        "success": False,
        "error": "Unknown model tool: workspace_run_shell",
    }
    assert result.summary == "The requested tool is unavailable."


@pytest.mark.anyio
async def test_tool_calling_round_limit_raises(
    monkeypatch,
    workspace_definitions,
):
    tool_call = {
        "name": "workspace_write_file",
        "args": {
            "target_file": "app/main.py",
            "content": "print('ok')",
        },
        "id": "call-loop",
        "type": "tool_call",
    }
    llm = FakeToolCallingLlm(
        [
            AIMessage(content="", tool_calls=[tool_call]),
            AIMessage(content="", tool_calls=[tool_call]),
        ]
    )

    async def fake_registry_call(request):
        return ToolCallResult(success=True, content="ok")

    monkeypatch.setattr(tool_registry, "call", fake_registry_call)

    with pytest.raises(
        RuntimeError,
        match="Agent exceeded maximum tool-calling rounds: 2",
    ):
        await run_tool_calling_agent(
            llm=llm,
            messages=[HumanMessage(content="Keep editing")],
            agent_code="backend",
            repo_path="/trusted/workspace",
            task_id=11,
            conversation_id=22,
            max_rounds=2,
        )


@pytest.mark.anyio
async def test_legacy_markers_execute_only_when_fallback_enabled(
    monkeypatch,
    workspace_definitions,
):
    content = "[FILE: app/main.py]\n```python\nprint('ok')\n```"
    llm = FakeToolCallingLlm([AIMessage(content=content)])
    fallback_calls = []

    async def fake_legacy_fallback(**kwargs):
        fallback_calls.append(kwargs)
        return ["app/main.py"]

    monkeypatch.setattr(
        "app.agents.tool_calling.apply_file_operations_with_tools",
        fake_legacy_fallback,
    )

    result = await run_tool_calling_agent(
        llm=llm,
        messages=[HumanMessage(content="Create app/main.py")],
        agent_code="backend",
        repo_path="/trusted/workspace",
        task_id=11,
        conversation_id=22,
        legacy_fallback=True,
        max_rounds=1,
    )

    assert len(fallback_calls) == 1
    assert fallback_calls[0]["local_path"] == "/trusted/workspace"
    assert result.changed_files == ["app/main.py"]
    assert result.used_legacy_fallback is True


@pytest.mark.anyio
async def test_disabled_legacy_fallback_never_invokes_parser(
    monkeypatch,
    workspace_definitions,
):
    content = "[FILE: app/main.py]\n```python\nprint('ok')\n```"
    llm = FakeToolCallingLlm([AIMessage(content=content)])

    async def forbidden_legacy_fallback(**kwargs):
        raise AssertionError("Legacy parser must remain disabled")

    monkeypatch.setattr(
        "app.agents.tool_calling.apply_file_operations_with_tools",
        forbidden_legacy_fallback,
    )

    result = await run_tool_calling_agent(
        llm=llm,
        messages=[HumanMessage(content="Create app/main.py")],
        agent_code="backend",
        repo_path="/trusted/workspace",
        task_id=11,
        conversation_id=22,
        legacy_fallback=False,
        max_rounds=1,
    )

    assert result.summary == content
    assert result.changed_files == []
    assert result.used_legacy_fallback is False


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("[FILE: app/main.py]", True),
        ("[DELETE: app/main.py]", True),
        ("[RENAME: old.py -> new.py]", True),
        ("Normal model response", False),
    ],
)
def test_contains_legacy_file_markers(content, expected):
    assert contains_legacy_file_markers(content) is expected


def test_legacy_text_protocol_is_documented_as_fallback_only():
    from app.services.workspace_service import WorkspaceService
    from app.tools import agent_file_ops

    module_doc = agent_file_ops.__doc__ or ""
    service_doc = WorkspaceService.apply_operations_from_text.__doc__ or ""
    readme = (
        Path(__file__).resolve().parents[1] / "README.md"
    ).read_text(encoding="utf-8")

    assert "Legacy compatibility parser" in module_doc
    assert "Primary agent execution uses native LLM Tool Calling" in module_doc
    assert "Deprecated" in service_doc
    assert "LLM Native Tool Calling" in readme
    assert "ToolCallRequest" in readme
    assert "ToolRegistry" in readme
    assert "Local / MCP / Hybrid" in readme
    assert "temporary compatibility fallback" in readme
