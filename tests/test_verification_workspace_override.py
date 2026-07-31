import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from git import Repo

from app.mcp.repository_resolver import ResolvedWorkspace
from app.services import repo_service
from app.services.command_runner import CommandExecutionResult
from app.services.verification_service import VerificationService


class Resolver:
    def __init__(self, path):
        self.path = path

    def resolve_owned_workspace(self, repository_id, user_id):
        return ResolvedWorkspace(repository_id, user_id, str(self.path))


class Runner:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return CommandExecutionResult(
            command_kind=kwargs["command_kind"],
            argv=["pytest"],
            exit_code=0,
            stdout="passed",
            stderr="",
            duration_ms=1,
            timed_out=False,
            truncated=False,
            success=True,
        )


def test_verification_runs_inside_owned_worktree(monkeypatch, tmp_path):
    original = tmp_path / "repository"
    original.mkdir()
    worktree_root = tmp_path / "worktrees"
    worktree = worktree_root / "user-7" / "repo-42" / "steps" / "backend"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: test", encoding="utf-8")
    (worktree / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    runner = Runner()
    service = VerificationService(
        repository_resolver=Resolver(original),
        command_runner=runner,
    )
    monkeypatch.setattr(
        "app.services.verification_service.settings",
        SimpleNamespace(
            resolved_agent_worktree_root=worktree_root.as_posix(),
        ),
    )

    result = service.verify(
        repository_id=42,
        user_id=7,
        changed_files=["app/api.py"],
        instruction="Implement.",
        workspace_path=str(worktree),
    )

    assert result.success is True
    assert runner.calls[0]["workspace_path"] == str(worktree.resolve())


def test_verification_rejects_unowned_or_non_git_override(
    monkeypatch,
    tmp_path,
):
    original = tmp_path / "repository"
    original.mkdir()
    root = tmp_path / "worktrees"
    outside = tmp_path / "outside"
    outside.mkdir()
    service = VerificationService(repository_resolver=Resolver(original))
    monkeypatch.setattr(
        "app.services.verification_service.settings",
        SimpleNamespace(resolved_agent_worktree_root=root.as_posix()),
    )

    with pytest.raises(ValueError, match="owned repository"):
        service.verify(
            repository_id=42,
            user_id=7,
            changed_files=["app/api.py"],
            instruction="Implement.",
            workspace_path=str(outside),
        )

    inside = root / "user-7" / "repo-42" / "steps" / "backend"
    inside.mkdir(parents=True)
    with pytest.raises(ValueError, match=".git"):
        service.verify(
            repository_id=42,
            user_id=7,
            changed_files=["app/api.py"],
            instruction="Implement.",
            workspace_path=str(inside),
        )


class FakeDb:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.flushes = 0
        self.refreshes = 0

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def flush(self):
        self.flushes += 1
        self.added[-1].id = 1

    def refresh(self, value):
        self.refreshes += 1
        value.id = 1

    def get(self, model, object_id):
        return None


def test_generate_code_change_reads_committed_range_without_checkout(
    monkeypatch,
    tmp_path,
):
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    repo = Repo.init(repository_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "AgentHub")
        config.set_value("user", "email", "agenthub@example.com")
    (repository_path / "base.txt").write_text("base\n", encoding="utf-8")
    repo.index.add(["base.txt"])
    base = repo.index.commit("base").hexsha
    repo.git.checkout("-b", "agent/orchestrator-5/integration")
    (repository_path / "result.txt").write_text("result\n", encoding="utf-8")
    repo.index.add(["result.txt"])
    result = repo.index.commit("result").hexsha
    task = SimpleNamespace(id=5, metadata_json=None)
    repository = SimpleNamespace(
        id=2,
        local_path=str(repository_path),
        repo_url="https://example.invalid/repo.git",
    )

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.task_service.broadcast_task_log",
        noop,
    )
    db = FakeDb()
    code_change = asyncio.run(
        repo_service.generate_code_change(
            db,
            task,
            repository,
            workspace_path=str(repository_path),
            branch_name="agent/orchestrator-5/integration",
            base_commit_hash=base,
            result_commit_hash=result,
        )
    )

    assert code_change.branch_name == "agent/orchestrator-5/integration"
    assert code_change.commit_hash == result
    assert code_change.changed_files == '["result.txt"]'
    assert "result.txt" in code_change.diff_text
    assert repo.active_branch.name == "agent/orchestrator-5/integration"
    assert repo.is_dirty(untracked_files=True) is False
    assert db.commits == 1
    assert db.flushes == 0
    assert db.refreshes == 1
    repo.close()


def test_generate_code_change_can_flush_without_committing(
    monkeypatch,
    tmp_path,
):
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    repo = Repo.init(repository_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "AgentHub")
        config.set_value("user", "email", "agenthub@example.com")
    (repository_path / "base.txt").write_text("base\n", encoding="utf-8")
    repo.index.add(["base.txt"])
    base = repo.index.commit("base").hexsha
    repo.git.checkout("-b", "agent/orchestrator-6/integration")
    (repository_path / "result.txt").write_text("result\n", encoding="utf-8")
    repo.index.add(["result.txt"])
    result = repo.index.commit("result").hexsha
    task = SimpleNamespace(id=6, metadata_json=None)
    repository = SimpleNamespace(
        id=2,
        local_path=str(repository_path),
        repo_url="https://example.invalid/repo.git",
    )

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.task_service.broadcast_task_log",
        noop,
    )
    db = FakeDb()
    code_change = asyncio.run(
        repo_service.generate_code_change(
            db,
            task,
            repository,
            workspace_path=str(repository_path),
            branch_name="agent/orchestrator-6/integration",
            base_commit_hash=base,
            result_commit_hash=result,
            auto_commit=False,
        )
    )

    assert code_change.id == 1
    assert db.commits == 0
    assert db.flushes == 1
    assert db.refreshes == 0
    repo.close()
