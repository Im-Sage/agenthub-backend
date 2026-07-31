from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from git import Repo

from app.services.orchestrator_dispatch_service import (
    build_orchestrator_canvas,
)
from app.services.orchestrator_execution_service import (
    StepExecutionOutcome,
)
from app.services.worktree_service import WorktreeHandle, WorktreeService
from app.workers.celery_app import celery_app


def _create_repository(path):
    repo = Repo.init(path, initial_branch="main")
    with repo.config_writer() as config:
        config.set_value("user", "name", "Integration Test")
        config.set_value("user", "email", "integration@example.com")
    (path / "shared.txt").write_text("base\n", encoding="utf-8")
    repo.index.add(["shared.txt"])
    repo.index.commit("initial")
    return repo


class CanvasHarness:
    def __init__(self, repository_path, worktree_root, *, conflict=False):
        self.repository_path = repository_path
        self.worktree_root = worktree_root
        self.conflict = conflict
        self.parent_id = 100
        self.integration = None
        self.handles = {}
        self.commits = {}
        self.statuses = {}
        self.merged_waves = set()
        self.agent_order = []
        self.code_changes = 0
        self.messages = 0

    def service(self):
        return WorktreeService(
            repository_id=1,
            user_id=1,
            repository_path=self.repository_path,
            worktree_root=self.worktree_root,
            lock_factory=lambda repository_id: nullcontext(),
        )

    def prepare(self, parent_id, generation=0):
        with self.service() as worktrees:
            base = worktrees.resolve_base_commit("main")
            self.integration = worktrees.ensure_integration_worktree(
                parent_id,
                base,
            )
        return {"status": "prepared"}

    def prepare_wave(self, parent_id, wave_index, generation=0):
        wave_steps = {
            0: ("backend", "frontend"),
            1: ("reviewer",),
        }[wave_index]
        with self.service() as worktrees:
            integration_head = worktrees.resolve_base_commit(
                self.integration.branch_name
            )
            for step in wave_steps:
                if self.statuses.get(step) == "SUCCESS":
                    continue
                self.handles[step] = worktrees.ensure_step_worktree(
                    parent_id,
                    step,
                    integration_head,
                )
        return {"status": "prepared"}

    def execute(self, child_id, celery_id, generation=0):
        step = {11: "backend", 12: "frontend", 13: "reviewer"}[child_id]
        if self.statuses.get(step) == "SUCCESS":
            commit = self.commits.get(step)
            return StepExecutionOutcome(
                child_id,
                "SUCCESS",
                commit,
                (),
                {"success": True},
                None,
            )
        handle = self.handles[step]
        path = handle.path
        if step == "reviewer":
            integration_path = Path(self.integration.path)
            assert (integration_path / "backend.txt").exists()
            assert (integration_path / "frontend.txt").exists()
            target = "review.txt"
            content = "reviewed after merge\n"
        elif self.conflict:
            target = "shared.txt"
            content = f"{step}\n"
        else:
            target = f"{step}.txt"
            content = f"{step} result\n"
        Path(path, target).write_text(content, encoding="utf-8")
        with self.service() as worktrees:
            committed = worktrees.commit_step_changes(
                handle,
                f"agent: {step}",
            )
        self.agent_order.append(step)
        self.commits[step] = committed.commit_hash
        self.statuses[step] = "SUCCESS"
        return StepExecutionOutcome(
            child_id,
            "SUCCESS",
            committed.commit_hash,
            committed.changed_files,
            {"success": True},
            None,
        )

    def merge(self, parent_id, wave_index, generation=0):
        if wave_index in self.merged_waves:
            return {"status": "merged", "idempotent": True}
        steps = {
            0: ("backend", "frontend"),
            1: ("reviewer",),
        }[wave_index]
        with self.service() as worktrees:
            for step in steps:
                result = worktrees.merge_step_commit(
                    self.integration,
                    self.commits[step],
                )
                if not result.success:
                    return {
                        "status": "conflict",
                        "conflict_files": list(result.conflict_files),
                    }
        self.merged_waves.add(wave_index)
        return {"status": "merged"}

    def finalize(self, parent_id, generation=0):
        if 0 not in self.merged_waves or 1 not in self.merged_waves:
            return {"status": "failed", "error": "wave conflict"}
        if self.code_changes == 0:
            self.code_changes += 1
            self.messages += 1
        return {"status": "success", "code_change_id": 1}


def _install_harness(monkeypatch, harness):
    from app.workers import orchestrator_tasks

    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "prepare_execution",
        harness.prepare,
    )
    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "prepare_wave",
        harness.prepare_wave,
    )
    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "execute_step",
        harness.execute,
    )
    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "merge_wave",
        harness.merge,
    )
    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "finalize_execution",
        harness.finalize,
    )


def _children():
    return [
        SimpleNamespace(
            id=11,
            step_key="backend",
            write_scope=["backend/**"],
        ),
        SimpleNamespace(
            id=12,
            step_key="frontend",
            write_scope=["frontend/**"],
        ),
        SimpleNamespace(
            id=13,
            step_key="reviewer",
            write_scope=["review/**"],
        ),
    ]


def test_eager_canvas_isolates_parallel_wave_and_is_idempotent(
    monkeypatch,
    tmp_path,
):
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    repo = _create_repository(repository_path)
    harness = CanvasHarness(
        repository_path,
        tmp_path / "worktrees",
    )
    _install_harness(monkeypatch, harness)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    waves = [
        {"index": 0, "step_ids": ["backend", "frontend"]},
        {"index": 1, "step_ids": ["reviewer"]},
    ]
    canvas = build_orchestrator_canvas(
        harness.parent_id,
        _children(),
        waves,
        execution_generation=1,
    )

    first = canvas.apply().get()
    second = canvas.apply().get()

    assert first == second == {"status": "success", "code_change_id": 1}
    assert harness.handles["backend"].path != harness.handles["frontend"].path
    assert harness.agent_order == ["backend", "frontend", "reviewer"]
    with Repo(harness.integration.path) as integration_repo:
        integration_path = Path(harness.integration.path)
        assert (integration_path / "backend.txt").read_text() == "backend result\n"
        assert (integration_path / "frontend.txt").read_text() == "frontend result\n"
        assert (integration_path / "review.txt").read_text() == "reviewed after merge\n"
        assert integration_repo.head.commit.hexsha != repo.head.commit.hexsha
        assert len(list(integration_repo.iter_commits())) == 4
    assert harness.code_changes == 1
    assert harness.messages == 1


def test_cherry_pick_conflict_preserves_integration_file(
    monkeypatch,
    tmp_path,
):
    repository_path = tmp_path / "conflict-repository"
    repository_path.mkdir()
    _create_repository(repository_path)
    harness = CanvasHarness(
        repository_path,
        tmp_path / "conflict-worktrees",
        conflict=True,
    )
    _install_harness(monkeypatch, harness)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    children = _children()[:2]
    assert children[0].write_scope != children[1].write_scope
    canvas = build_orchestrator_canvas(
        harness.parent_id,
        children,
        [{"index": 0, "step_ids": ["backend", "frontend"]}],
        execution_generation=1,
    )

    result = canvas.apply().get()

    assert result["status"] == "failed"
    shared = Path(harness.integration.path, "shared.txt")
    assert shared.read_text(encoding="utf-8") == "backend\n"
    assert harness.code_changes == 0
