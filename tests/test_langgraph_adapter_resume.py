import asyncio
from contextlib import asynccontextmanager

from langgraph.types import Command

from app.agents.graph.runtime import graph_config


def test_adapter_resume_uses_command_and_same_task_config(monkeypatch):
    from app.agents import langgraph_adapter

    invocations = []

    class FakeGraph:
        async def ainvoke(self, graph_input, config):
            invocations.append((graph_input, config))
            return {
                "plan": [{"agent": "backend", "instruction": "Implement"}],
                "execution_results": [],
                "awaiting_confirmation": False,
                "final_summary": "workflow completed",
            }

    @asynccontextmanager
    async def fake_open_agent_graph():
        yield FakeGraph()

    monkeypatch.setattr(
        langgraph_adapter,
        "open_agent_graph",
        fake_open_agent_graph,
    )

    resume_value = {"approved": True}
    result = asyncio.run(
        langgraph_adapter.LangGraphOrchestratorAdapter().resume(
            task_id=123,
            resume_value=resume_value,
        )
    )

    assert len(invocations) == 1
    graph_input, config = invocations[0]
    assert isinstance(graph_input, Command)
    assert graph_input.resume == resume_value
    assert config == graph_config(123)
    assert result.status == "success"
    assert result.summary == "workflow completed"


def test_adapter_success_result_collects_changed_files_in_order(monkeypatch):
    from app.agents import langgraph_adapter

    class FakeGraph:
        async def ainvoke(self, graph_input, config):
            return {
                "plan": [
                    {"agent": "backend", "instruction": "Implement"},
                    {"agent": "reviewer", "instruction": "Review"},
                ],
                "execution_results": [
                    {"files": ["app/a.py", "app/b.py"]},
                    {"files": ["app/a.py"]},
                ],
                "awaiting_confirmation": False,
                "final_summary": "done",
            }

    @asynccontextmanager
    async def fake_open_agent_graph():
        yield FakeGraph()

    monkeypatch.setattr(
        langgraph_adapter,
        "open_agent_graph",
        fake_open_agent_graph,
    )

    result = asyncio.run(
        langgraph_adapter.LangGraphOrchestratorAdapter().resume(
            task_id=456,
            resume_value={"approved": True},
        )
    )

    assert result.status == "success"
    assert result.summary == "done"
    assert result.changed_files == ["app/a.py", "app/b.py"]
    assert result.logs == "LangGraph executed 2 steps."
