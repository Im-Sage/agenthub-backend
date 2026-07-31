import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.graph import nodes, workflow
from app.agents.graph.runtime import graph_config
from app.agents.graph.state import AgentState
from app.agents.langgraph_adapter import LangGraphOrchestratorAdapter
from app.agents.base import AgentRunResult
from app.schemas.enums import TaskStatus


def _initial_state(task_id=42):
    return {
        "messages": [HumanMessage(content="Build dispatched execution")],
        "task_id": task_id,
        "conversation_id": 7,
        "repo_path": None,
        "repository_id": None,
        "user_id": None,
        "plan": [],
        "current_step_index": 0,
        "current_agent": None,
        "current_instruction": None,
        "execution_results": [],
        "verification_results": [],
        "verification_attempts": 0,
        "errors": [],
        "awaiting_confirmation": False,
        "approval_status": None,
        "execution_dispatched": False,
        "canvas_id": None,
        "is_finished": False,
        "final_summary": None,
        "metadata_json": None,
    }


def test_agent_state_declares_dispatch_result():
    assert AgentState.__annotations__["execution_dispatched"] is bool
    assert AgentState.__annotations__["canvas_id"] == str | None


def test_active_graph_contains_no_inline_executor_or_verifier():
    graph = workflow.create_agent_graph()

    assert "dispatcher" in graph.nodes
    assert "reject_plan" in graph.nodes
    assert "executor" not in graph.nodes
    assert "verifier" not in graph.nodes


def test_graph_preserves_interrupt_and_dispatches_on_same_thread(monkeypatch):
    calls = {"planner": 0, "dispatcher": 0, "reject": 0}

    async def planner(state):
        calls["planner"] += 1
        return {
            "plan": [
                {
                    "id": "backend",
                    "agent": "backend",
                    "instruction": "Implement API",
                    "depends_on": [],
                    "write_scope": ["app/**"],
                }
            ],
            "awaiting_confirmation": True,
            "approval_status": None,
        }

    async def dispatcher(state):
        calls["dispatcher"] += 1
        return {
            "execution_dispatched": True,
            "canvas_id": "canvas-42",
        }

    async def reject(state):
        calls["reject"] += 1
        return {"is_finished": True}

    monkeypatch.setattr(workflow, "plan_node", planner)
    monkeypatch.setattr(workflow, "dispatch_node", dispatcher)
    monkeypatch.setattr(workflow, "reject_plan_node", reject)
    graph = workflow.create_agent_graph(checkpointer=InMemorySaver())
    config = graph_config(42)

    interrupted = asyncio.run(
        graph.ainvoke(_initial_state(), config=config)
    )
    assert interrupted["__interrupt__"]
    assert calls == {"planner": 1, "dispatcher": 0, "reject": 0}

    resumed = asyncio.run(
        graph.ainvoke(
            Command(resume={"approved": True}),
            config=config,
        )
    )

    assert calls == {"planner": 1, "dispatcher": 1, "reject": 0}
    assert resumed["execution_dispatched"] is True
    assert resumed["canvas_id"] == "canvas-42"


def test_dispatch_node_only_enqueues(monkeypatch):
    from app.services import orchestrator_dispatch_service

    observed = []
    monkeypatch.setattr(
        orchestrator_dispatch_service,
        "dispatch_orchestrator_execution",
        lambda task_id: observed.append(task_id)
        or {"status": "dispatched", "canvas_id": "canvas-9"},
    )

    result = asyncio.run(nodes.dispatch_node({"task_id": 9}))

    assert observed == [9]
    assert result == {
        "execution_dispatched": True,
        "canvas_id": "canvas-9",
        "is_finished": True,
        "errors": [],
    }


def test_adapter_maps_dispatch_state_without_waiting(monkeypatch):
    from app.agents import langgraph_adapter

    class FakeGraph:
        async def ainvoke(self, graph_input, config):
            assert isinstance(graph_input, Command)
            assert config == graph_config(77)
            return {
                "execution_dispatched": True,
                "canvas_id": "canvas-77",
            }

    @asynccontextmanager
    async def open_graph():
        yield FakeGraph()

    monkeypatch.setattr(
        langgraph_adapter,
        "open_agent_graph",
        open_graph,
    )

    result = asyncio.run(
        LangGraphOrchestratorAdapter().resume(
            77,
            {"approved": True},
        )
    )

    assert result.status == "dispatched"
    assert result.summary == "Orchestrator execution dispatched to Celery."
    assert result.changed_files == []
    assert result.logs == "Celery canvas id: canvas-77"


class RejectDb:
    def __init__(self, parent, children):
        self.parent = parent
        self.children = children
        self.commits = 0

    def get(self, model, task_id):
        return self.parent

    def scalars(self, statement):
        return self.children

    def commit(self):
        self.commits += 1

    def close(self):
        return None


def test_reject_plan_cancels_parent_and_unstarted_children(monkeypatch):
    parent = SimpleNamespace(
        id=42,
        status=TaskStatus.PENDING,
        metadata_json='{"plan_status": "awaiting_confirmation"}',
        finished_at=None,
        error_message=None,
    )
    pending = SimpleNamespace(status=TaskStatus.PENDING, finished_at=None)
    running = SimpleNamespace(status=TaskStatus.RUNNING, finished_at=None)
    db = RejectDb(parent, [pending, running])
    events = []

    async def broadcast(task, event):
        events.append((task, event))

    monkeypatch.setattr(nodes, "SessionLocal", lambda: db)
    from app.services import task_service

    monkeypatch.setattr(task_service, "broadcast_task_event", broadcast)

    result = asyncio.run(nodes.reject_plan_node({"task_id": 42}))

    assert parent.status == TaskStatus.CANCELLED
    assert pending.status == TaskStatus.CANCELLED
    assert running.status == TaskStatus.RUNNING
    assert result["is_finished"] is True
    assert result["execution_dispatched"] is False
    assert db.commits == 1


def test_resume_worker_keeps_dispatched_parent_running(monkeypatch):
    from app.workers import agent_tasks

    parent = SimpleNamespace(
        id=55,
        status=TaskStatus.PENDING,
        finished_at=object(),
        error_message=None,
    )

    class Db:
        def __init__(self):
            self.commits = 0

        def get(self, model, task_id):
            assert task_id == 55
            return parent

        def commit(self):
            self.commits += 1

        def refresh(self, value):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    class Adapter:
        async def resume(self, task_id, resume_value):
            return AgentRunResult(
                status="dispatched",
                summary="Orchestrator execution dispatched to Celery.",
                changed_files=[],
                logs="Celery canvas id: canvas-55",
            )

    async def broadcast(task, event):
        return None

    db = Db()
    monkeypatch.setattr(agent_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        agent_tasks,
        "LangGraphOrchestratorAdapter",
        Adapter,
    )
    from app.services import task_service

    monkeypatch.setattr(task_service, "broadcast_task_event", broadcast)

    result = agent_tasks.resume_orchestrator_task.run(
        55,
        {"approved": True},
    )

    assert parent.status == TaskStatus.RUNNING
    assert parent.finished_at is None
    assert db.commits == 2
    assert result == (
        "LangGraph Orchestrator resumed: "
        "Orchestrator execution dispatched to Celery."
    )
