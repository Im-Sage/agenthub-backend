import asyncio
from types import SimpleNamespace

from app.core import config


def test_resolved_checkpoint_path_anchors_relative_path_to_project_root(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    settings = config.Settings(
        langgraph_checkpoint_path="./checkpoints/agenthub.sqlite3",
    )

    assert settings.resolved_langgraph_checkpoint_path == (
        tmp_path / "checkpoints" / "agenthub.sqlite3"
    ).resolve().as_posix()


def test_resolved_checkpoint_path_preserves_absolute_path(tmp_path):
    checkpoint_path = (tmp_path / "agenthub.sqlite3").resolve()
    settings = config.Settings(
        langgraph_checkpoint_path=str(checkpoint_path),
    )

    assert settings.resolved_langgraph_checkpoint_path == checkpoint_path.as_posix()


def test_graph_thread_id_uses_business_task_id():
    from app.agents.graph.runtime import graph_thread_id

    assert graph_thread_id(123) == "orchestrator-task-123"


def test_graph_config_uses_stable_thread_id():
    from app.agents.graph.runtime import graph_config

    assert graph_config(123)["configurable"]["thread_id"] == (
        "orchestrator-task-123"
    )


def test_graph_thread_ids_are_isolated_by_business_task():
    from app.agents.graph.runtime import graph_thread_id

    assert graph_thread_id(123) != graph_thread_id(124)


def test_open_agent_graph_creates_parent_and_binds_checkpointer(
    monkeypatch,
    tmp_path,
):
    from app.agents.graph import runtime

    checkpoint_path = tmp_path / "nested" / "agenthub.sqlite3"
    checkpointer = object()
    compiled_graph = object()
    observed = {}

    class FakeCheckpointerContext:
        async def __aenter__(self):
            return checkpointer

        async def __aexit__(self, exc_type, exc, traceback):
            observed["closed"] = True

    def fake_from_conn_string(connection_string):
        observed["connection_string"] = connection_string
        return FakeCheckpointerContext()

    def fake_create_agent_graph(*, checkpointer):
        observed["checkpointer"] = checkpointer
        return compiled_graph

    monkeypatch.setattr(
        runtime,
        "settings",
        SimpleNamespace(
            resolved_langgraph_checkpoint_path=checkpoint_path.as_posix(),
        ),
    )
    monkeypatch.setattr(
        runtime.AsyncSqliteSaver,
        "from_conn_string",
        fake_from_conn_string,
    )
    monkeypatch.setattr(runtime, "create_agent_graph", fake_create_agent_graph)

    async def exercise_runtime():
        async with runtime.open_agent_graph() as graph:
            assert graph is compiled_graph
            assert checkpoint_path.parent.is_dir()
            assert observed["checkpointer"] is checkpointer

    asyncio.run(exercise_runtime())

    assert observed["connection_string"] == checkpoint_path.as_posix()
    assert observed["closed"] is True
