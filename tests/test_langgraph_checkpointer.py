from pathlib import Path

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
