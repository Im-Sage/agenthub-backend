import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core import config


def test_checkpoint_backend_defaults_to_sqlite():
    configured = config.Settings(_env_file=None)

    assert configured.langgraph_checkpoint_backend == "sqlite"
    assert configured.langgraph_checkpoint_database_url is None
    assert configured.langgraph_checkpoint_auto_setup is True


def test_postgres_checkpoint_backend_requires_database_url():
    with pytest.raises(
        ValidationError,
        match="langgraph_checkpoint_database_url is required",
    ):
        config.Settings(
            _env_file=None,
            langgraph_checkpoint_backend="postgres",
            langgraph_checkpoint_database_url=None,
        )


def test_resolved_agent_worktree_root_anchors_relative_path_to_project_root(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    configured = config.Settings(
        _env_file=None,
        agent_worktree_root="./isolated-worktrees",
    )

    assert configured.resolved_agent_worktree_root == (
        tmp_path / "isolated-worktrees"
    ).resolve().as_posix()


def test_resolved_agent_worktree_root_preserves_absolute_path(tmp_path):
    absolute_root = (tmp_path / "isolated-worktrees").resolve()
    configured = config.Settings(
        _env_file=None,
        agent_worktree_root=str(absolute_root),
    )

    assert configured.resolved_agent_worktree_root == Path(
        absolute_root
    ).as_posix()


def test_open_checkpointer_uses_sqlite_saver(monkeypatch, tmp_path):
    from app.agents.graph import checkpointer

    checkpoint_path = tmp_path / "nested" / "checkpoints.sqlite3"
    sqlite_saver = object()
    observed = {}

    class SaverContext:
        async def __aenter__(self):
            return sqlite_saver

        async def __aexit__(self, exc_type, exc, traceback):
            observed["closed"] = True

    def fake_from_conn_string(connection_string):
        observed["connection_string"] = connection_string
        return SaverContext()

    monkeypatch.setattr(
        checkpointer,
        "settings",
        SimpleNamespace(
            langgraph_checkpoint_backend="sqlite",
            resolved_langgraph_checkpoint_path=checkpoint_path.as_posix(),
        ),
    )
    monkeypatch.setattr(
        checkpointer.AsyncSqliteSaver,
        "from_conn_string",
        fake_from_conn_string,
    )

    async def exercise():
        async with checkpointer.open_checkpointer() as saver:
            assert saver is sqlite_saver
            assert checkpoint_path.parent.is_dir()

    asyncio.run(exercise())

    assert observed == {
        "connection_string": checkpoint_path.as_posix(),
        "closed": True,
    }


def test_open_checkpointer_uses_postgres_dsn(monkeypatch):
    from app.agents.graph import checkpointer

    dsn = "postgresql://agenthub:secret@db/agenthub_checkpoints"
    postgres_saver = object()
    observed = {}

    class SaverContext:
        async def __aenter__(self):
            return postgres_saver

        async def __aexit__(self, exc_type, exc, traceback):
            observed["closed"] = True

    def fake_from_conn_string(connection_string):
        observed["connection_string"] = connection_string
        return SaverContext()

    monkeypatch.setattr(
        checkpointer,
        "settings",
        SimpleNamespace(
            langgraph_checkpoint_backend="postgres",
            langgraph_checkpoint_database_url=dsn,
            langgraph_checkpoint_auto_setup=False,
        ),
    )
    monkeypatch.setattr(
        checkpointer.AsyncPostgresSaver,
        "from_conn_string",
        fake_from_conn_string,
    )

    async def exercise():
        async with checkpointer.open_checkpointer() as saver:
            assert saver is postgres_saver

    asyncio.run(exercise())

    assert observed == {
        "connection_string": dsn,
        "closed": True,
    }


def test_postgres_checkpointer_setup_runs_once_per_process(monkeypatch):
    from app.agents.graph import checkpointer

    dsn = "postgresql://agenthub:secret@db/agenthub_checkpoints"
    setup_calls = 0

    class FakeSaver:
        async def setup(self):
            nonlocal setup_calls
            setup_calls += 1

    class SaverContext:
        async def __aenter__(self):
            return FakeSaver()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    monkeypatch.setattr(
        checkpointer,
        "settings",
        SimpleNamespace(
            langgraph_checkpoint_backend="postgres",
            langgraph_checkpoint_database_url=dsn,
            langgraph_checkpoint_auto_setup=True,
        ),
    )
    monkeypatch.setattr(
        checkpointer.AsyncPostgresSaver,
        "from_conn_string",
        lambda connection_string: SaverContext(),
    )
    monkeypatch.setattr(checkpointer, "_postgres_setup_complete", False)

    async def exercise():
        async with checkpointer.open_checkpointer():
            pass
        async with checkpointer.open_checkpointer():
            pass

    asyncio.run(exercise())

    assert setup_calls == 1
