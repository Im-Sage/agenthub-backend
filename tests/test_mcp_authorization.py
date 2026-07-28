from contextlib import asynccontextmanager
import hmac
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.agents.tool_calling import build_model_tools, run_tool_calling_agent
from app.mcp.auth import InternalBearerAuthMiddleware
from app.mcp.repository_resolver import (
    RepositoryResolver,
    ResolvedWorkspace,
    WorkspaceAuthorizationError,
)
from app.tools.base import (
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
    ToolRiskLevel,
)
from app.tools.registry import ToolRegistry, tool_registry


async def ok_endpoint(request):
    return JSONResponse({"ok": True})


def auth_app(token="internal-token"):
    inner = Starlette(routes=[Route("/mcp", ok_endpoint)])
    return InternalBearerAuthMiddleware(inner, token)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic internal-token"},
        {"Authorization": "Bearer wrong-token"},
    ],
)
async def test_mcp_auth_rejects_missing_or_invalid_bearer_token(headers):
    transport = httpx.ASGITransport(app=auth_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/mcp", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized"}


@pytest.mark.anyio
async def test_mcp_auth_accepts_matching_token_with_constant_time_compare(
    monkeypatch,
):
    comparisons = []
    real_compare = hmac.compare_digest

    def compare_digest(left, right):
        comparisons.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr("app.mcp.auth.hmac.compare_digest", compare_digest)
    transport = httpx.ASGITransport(app=auth_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/mcp",
            headers={"Authorization": "Bearer internal-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert comparisons == [("internal-token", "internal-token")]


def test_mcp_auth_rejects_empty_server_token():
    with pytest.raises(RuntimeError, match="MCP internal token"):
        auth_app("")


class FakeSession:
    def __init__(self, repository):
        self.repository = repository
        self.closed = False

    def get(self, model, repository_id):
        if self.repository and self.repository.id == repository_id:
            return self.repository
        return None

    def close(self):
        self.closed = True


def test_repository_resolver_enforces_repository_ownership(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    repository = SimpleNamespace(
        id=21,
        user_id=7,
        local_path=str(workspace),
    )
    session = FakeSession(repository)
    resolver = RepositoryResolver(session_factory=lambda: session)

    resolved = resolver.resolve_owned_workspace(21, 7)

    assert resolved == ResolvedWorkspace(
        repository_id=21,
        user_id=7,
        local_path=str(workspace),
    )
    assert session.closed is True

    with pytest.raises(
        WorkspaceAuthorizationError,
        match="Repository access denied",
    ):
        resolver.resolve_owned_workspace(21, 8)

    with pytest.raises(
        WorkspaceAuthorizationError,
        match="Repository not found",
    ):
        resolver.resolve_owned_workspace(999, 7)


@pytest.mark.parametrize(
    ("repository_id", "user_id"),
    [(0, 1), (1, 0), (-1, 1), (1, -1)],
)
def test_repository_resolver_rejects_non_positive_ids(
    repository_id,
    user_id,
):
    resolver = RepositoryResolver(
        session_factory=lambda: pytest.fail("database must not be queried")
    )

    with pytest.raises(WorkspaceAuthorizationError):
        resolver.resolve_owned_workspace(repository_id, user_id)


def workspace_definition():
    return ToolDefinition(
        name="workspace.write_file",
        description="Write a workspace file",
        risk_level=ToolRiskLevel.MEDIUM,
        input_schema={
            "type": "object",
            "properties": {
                "local_path": {"type": "string"},
                "repository_id": {"type": "integer"},
                "user_id": {"type": "integer"},
                "target_file": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": [
                "local_path",
                "repository_id",
                "user_id",
                "target_file",
                "content",
            ],
        },
    )


def test_model_schema_hides_all_trusted_workspace_parameters(monkeypatch):
    monkeypatch.setattr(
        tool_registry,
        "list_tools",
        lambda: [workspace_definition()],
    )

    tools, _ = build_model_tools("backend", has_workspace=True)
    parameters = tools[0]["function"]["parameters"]

    assert set(parameters["properties"]) == {"target_file", "content"}
    assert set(parameters["required"]) == {"target_file", "content"}


class FakeToolCallingLlm:
    def __init__(self):
        self.bound_tools = None
        self.responses = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_write_file",
                        "args": {
                            "local_path": "C:/attacker",
                            "repository_id": 999,
                            "user_id": 999,
                            "target_file": "app/main.py",
                            "content": "print('safe')",
                        },
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Implemented app/main.py"),
        ]

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        return self.responses.pop(0)


@pytest.mark.anyio
async def test_tool_calling_overrides_forged_workspace_identity(monkeypatch):
    llm = FakeToolCallingLlm()
    requests = []
    monkeypatch.setattr(
        tool_registry,
        "list_tools",
        lambda: [workspace_definition()],
    )

    async def capture(request):
        requests.append(request)
        return ToolCallResult(
            success=True,
            structured_content={"changed_files": ["app/main.py"]},
        )

    monkeypatch.setattr(tool_registry, "call", capture)

    result = await run_tool_calling_agent(
        llm=llm,
        messages=[HumanMessage(content="Implement the entry point")],
        agent_code="backend",
        repo_path=None,
        repository_id=42,
        user_id=7,
        task_id=11,
        conversation_id=22,
        max_rounds=2,
    )

    request = requests[0]
    assert request.repository_id == 42
    assert request.user_id == 7
    assert "local_path" not in request.arguments
    assert "repository_id" not in request.arguments
    assert "user_id" not in request.arguments
    assert result.changed_files == ["app/main.py"]


@pytest.mark.anyio
async def test_mcp_registry_injects_trusted_repository_identity(monkeypatch):
    from app.tools import registry as registry_module

    calls = []

    class FakeMCPToolClient:
        def __init__(self, server_url, token=None):
            self.server_url = server_url
            self.token = token

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return {"success": True, "content": "ok"}

    monkeypatch.setattr(
        registry_module.settings,
        "mcp_workspace_server_url",
        "http://127.0.0.1:9000/mcp",
    )
    monkeypatch.setattr(
        registry_module.settings,
        "mcp_internal_token",
        "internal-token",
    )
    monkeypatch.setattr(
        registry_module,
        "MCPToolClient",
        FakeMCPToolClient,
    )
    registry = ToolRegistry()

    result = await registry._call_mcp(
        ToolCallRequest(
            name="workspace.read_file",
            arguments={
                "repository_id": 999,
                "user_id": 999,
                "local_path": "C:/attacker",
                "target_file": "README.md",
            },
            repository_id=42,
            user_id=7,
        )
    )

    assert result.success is True
    assert calls == [
        (
            "workspace_read_file",
            {
                "target_file": "README.md",
                "repository_id": 42,
                "user_id": 7,
            },
        )
    ]


@pytest.mark.anyio
async def test_mcp_workspace_tools_hide_paths_and_delete_tool():
    from app.mcp.workspace_server import mcp

    tools = await mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    assert "workspace_delete_file" not in by_name
    read_schema = by_name["workspace_read_file"].inputSchema
    assert "local_path" not in read_schema["properties"]
    assert {"repository_id", "user_id", "target_file"} <= set(
        read_schema["properties"]
    )


@pytest.mark.anyio
async def test_local_workspace_tools_use_resolved_path_and_reject_traversal(
    monkeypatch,
    tmp_path,
):
    from app.tools import workspace_tools

    workspace = tmp_path / "repo"
    workspace.mkdir()

    class FakeResolver:
        def resolve_owned_workspace(self, repository_id, user_id):
            assert (repository_id, user_id) == (42, 7)
            return ResolvedWorkspace(42, 7, str(workspace))

    monkeypatch.setattr(
        workspace_tools,
        "repository_resolver",
        FakeResolver(),
    )
    write_request = ToolCallRequest(
        name="workspace.write_file",
        arguments={
            "local_path": "C:/attacker",
            "target_file": "src/app.py",
            "content": "print('safe')",
        },
        repository_id=42,
        user_id=7,
    )

    written = await workspace_tools.workspace_write_file(write_request)
    read = await workspace_tools.workspace_read_file(
        ToolCallRequest(
            name="workspace.read_file",
            arguments={"target_file": "src/app.py"},
            repository_id=42,
            user_id=7,
        )
    )
    escaped = await workspace_tools.workspace_write_file(
        ToolCallRequest(
            name="workspace.write_file",
            arguments={
                "target_file": "../escape.py",
                "content": "unsafe",
            },
            repository_id=42,
            user_id=7,
        )
    )

    assert written.success is True
    assert read.content == "print('safe')"
    assert escaped.success is False
    assert not (tmp_path / "escape.py").exists()


@pytest.mark.anyio
async def test_qwen_adapter_passes_trusted_repository_identity(monkeypatch):
    from app.agents import qwen_adapter
    from app.agents.base import AgentRunRequest
    from app.agents.tool_calling import ToolCallingRunResult
    from app.core.config import settings

    captured = {}

    async def fake_run_tool_calling_agent(**kwargs):
        captured.update(kwargs)
        return ToolCallingRunResult(summary="done")

    monkeypatch.setattr(settings, "aliyun_api_key", "test-key")
    monkeypatch.setattr(qwen_adapter, "get_chat_llm", object)
    monkeypatch.setattr(
        qwen_adapter,
        "run_tool_calling_agent",
        fake_run_tool_calling_agent,
    )

    await qwen_adapter.QwenAgentAdapter().run(
        AgentRunRequest(
            task_id=11,
            conversation_id=22,
            instruction="Inspect the repository",
            repo_path="C:/internal-only",
            repository_id=42,
            user_id=7,
        )
    )

    assert captured["repository_id"] == 42
    assert captured["user_id"] == 7


@pytest.mark.anyio
async def test_langgraph_adapter_checkpoints_trusted_repository_identity(
    monkeypatch,
):
    from app.agents import langgraph_adapter
    from app.agents.base import AgentRunRequest

    invocations = []

    class FakeGraph:
        async def ainvoke(self, graph_input, config):
            invocations.append(graph_input)
            return {
                **graph_input,
                "awaiting_confirmation": True,
            }

    @asynccontextmanager
    async def fake_open_agent_graph():
        yield FakeGraph()

    monkeypatch.setattr(
        langgraph_adapter,
        "open_agent_graph",
        fake_open_agent_graph,
    )

    await langgraph_adapter.LangGraphOrchestratorAdapter().run(
        AgentRunRequest(
            task_id=11,
            conversation_id=22,
            instruction="Plan repository changes",
            repo_path="C:/internal-only",
            repository_id=42,
            user_id=7,
        )
    )

    assert invocations[0]["repository_id"] == 42
    assert invocations[0]["user_id"] == 7


def test_orchestrator_worker_injects_repository_owner_identity(monkeypatch):
    from app.agents.base import AgentRunResult
    from app.models import Agent, Conversation, Repository, Task
    from app.schemas.enums import TaskStatus
    from app.workers import agent_tasks

    parent_task = SimpleNamespace(
        id=11,
        agent_id=12,
        conversation_id=13,
        instruction="Plan the change",
        status=TaskStatus.PENDING,
        started_at=None,
        finished_at=None,
    )
    agent = SimpleNamespace(id=12, system_prompt="Plan safely")
    conversation = SimpleNamespace(id=13, repository_id=42)
    repository = SimpleNamespace(
        id=42,
        user_id=7,
        local_path="C:/internal-only",
    )

    class FakeDb:
        def get(self, model, object_id):
            return {
                (Task, 11): parent_task,
                (Agent, 12): agent,
                (Conversation, 13): conversation,
                (Repository, 42): repository,
            }.get((model, object_id))

        def commit(self):
            return None

        def refresh(self, value):
            return None

        def close(self):
            return None

    requests = []

    class FakeAdapter:
        async def run(self, request):
            requests.append(request)
            return AgentRunResult(
                status="awaiting_confirmation",
                summary="waiting",
            )

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_tasks, "SessionLocal", FakeDb)
    monkeypatch.setattr(
        agent_tasks.task_service,
        "get_adapter",
        lambda value: FakeAdapter(),
    )
    monkeypatch.setattr(
        agent_tasks.task_service,
        "broadcast_task_event",
        noop,
    )

    agent_tasks.run_orchestrator_task.run(parent_task.id)

    assert requests[0].repository_id == 42
    assert requests[0].user_id == 7
