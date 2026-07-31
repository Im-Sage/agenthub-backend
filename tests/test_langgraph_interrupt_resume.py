import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.base import AgentRunRequest
from app.agents.graph import workflow
from app.agents.graph.runtime import graph_config
from app.agents.graph.state import AgentState


def initial_state(task_id: int = 42) -> dict:
    return {
        "messages": [HumanMessage(content="Build durable approval")],
        "task_id": task_id,
        "conversation_id": 7,
        "repo_path": None,
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


def install_fake_workflow_nodes(monkeypatch):
    calls = {
        "planner": [],
        "dispatcher": [],
        "reject_plan": [],
    }
    plan = [{"agent": "backend", "instruction": "Implement durable HITL"}]

    async def fake_planner(state):
        calls["planner"].append(state["task_id"])
        return {
            "plan": plan,
            "current_step_index": 0,
            "current_agent": "backend",
            "current_instruction": "Implement durable HITL",
            "metadata_json": '{"child_ids": [900]}',
            "awaiting_confirmation": True,
            "approval_status": None,
            "errors": [],
            "is_finished": False,
            "final_summary": None,
        }

    async def fake_dispatcher(state):
        calls["dispatcher"].append(state["task_id"])
        return {
            "execution_dispatched": True,
            "canvas_id": f"canvas-{state['task_id']}",
            "is_finished": True,
            "errors": [],
        }

    async def fake_reject_plan(state):
        calls["reject_plan"].append(state["task_id"])
        return {
            "is_finished": True,
            "execution_dispatched": False,
            "errors": [],
        }

    monkeypatch.setattr(workflow, "plan_node", fake_planner)
    monkeypatch.setattr(workflow, "dispatch_node", fake_dispatcher)
    monkeypatch.setattr(workflow, "reject_plan_node", fake_reject_plan)
    return SimpleNamespace(calls=calls, plan=plan)


def test_agent_state_uses_messages_channel():
    assert "messages" in AgentState.__annotations__
    assert "messgaes" not in AgentState.__annotations__


def test_graph_interrupts_before_dispatcher_and_resumes_same_thread(
    monkeypatch,
):
    observed = install_fake_workflow_nodes(monkeypatch)
    graph = workflow.create_agent_graph(checkpointer=InMemorySaver())
    config = graph_config(42)

    interrupted = asyncio.run(graph.ainvoke(initial_state(), config=config))

    assert interrupted["__interrupt__"]
    assert interrupted["awaiting_confirmation"] is True
    assert observed.calls["planner"] == [42]
    assert observed.calls["dispatcher"] == []

    resumed = asyncio.run(
        graph.ainvoke(Command(resume={"approved": True}), config=config)
    )

    assert observed.calls["planner"] == [42]
    assert observed.calls["dispatcher"] == [42]
    assert observed.calls["reject_plan"] == []
    assert resumed["approval_status"] == "approved"
    assert resumed["execution_dispatched"] is True
    assert resumed["canvas_id"] == "canvas-42"


def test_different_thread_cannot_read_interrupted_task_state(monkeypatch):
    install_fake_workflow_nodes(monkeypatch)
    graph = workflow.create_agent_graph(checkpointer=InMemorySaver())

    asyncio.run(graph.ainvoke(initial_state(42), config=graph_config(42)))
    other_thread = asyncio.run(graph.aget_state(graph_config(43)))

    assert other_thread.values == {}
    assert other_thread.next == ()


def test_rejected_approval_ends_without_dispatcher(monkeypatch):
    observed = install_fake_workflow_nodes(monkeypatch)
    graph = workflow.create_agent_graph(checkpointer=InMemorySaver())
    config = graph_config(44)

    asyncio.run(graph.ainvoke(initial_state(44), config=config))
    rejected = asyncio.run(
        graph.ainvoke(Command(resume={"approved": False}), config=config)
    )

    assert rejected["approval_status"] == "rejected"
    assert rejected["is_finished"] is True
    assert observed.calls["dispatcher"] == []
    assert observed.calls["reject_plan"] == [44]


def test_workflow_does_not_expose_uncheckpointed_global_graph():
    assert not hasattr(workflow, "agent_graph")


def test_adapter_start_uses_persistent_graph_and_task_config(monkeypatch):
    from app.agents import langgraph_adapter

    invocations = []

    class FakeGraph:
        async def ainvoke(self, graph_input, config):
            invocations.append((graph_input, config))
            return {
                **graph_input,
                "plan": [{"agent": "backend", "instruction": "Implement"}],
                "awaiting_confirmation": True,
                "final_summary": None,
            }

    @asynccontextmanager
    async def fake_open_agent_graph():
        yield FakeGraph()

    monkeypatch.setattr(
        langgraph_adapter,
        "open_agent_graph",
        fake_open_agent_graph,
    )

    request = AgentRunRequest(
        task_id=123,
        conversation_id=7,
        instruction="Build durable approval",
    )
    result = asyncio.run(
        langgraph_adapter.LangGraphOrchestratorAdapter().run(request)
    )

    assert len(invocations) == 1
    graph_input, config = invocations[0]
    assert graph_input["approval_status"] is None
    assert config == graph_config(123)
    assert result.status == "awaiting_confirmation"
    assert result.summary == (
        "Orchestrator plan generated and is awaiting confirmation."
    )
    assert result.changed_files == []
    assert result.logs == "LangGraph interrupted for human approval."
