import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.models.code_change import CodeChange
from app.models.task import Task
from app.schemas.enums import CodeChangeStatus, TaskStatus
from app.services import orchestrator_dispatch_service
from app.services import orchestrator_recovery_service as service


class FakeSession:
    def __init__(self, parent, children=(), code_change=None):
        self.parent = parent
        self.children = list(children)
        self.code_change = code_change
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def get(self, model, object_id):
        if model is Task:
            return self.parent
        if model is CodeChange:
            return self.code_change
        return None

    def scalars(self, statement):
        return self.children

    def commit(self):
        self.commits += 1


def _parent(status, metadata):
    return SimpleNamespace(
        id=100,
        status=status,
        metadata_json=json.dumps(metadata),
        celery_task_id="resume-task",
        error_message=None,
        finished_at=None,
    )


def _child(child_id, status, **overrides):
    defaults = {
        "id": child_id,
        "status": status,
        "celery_task_id": None,
        "step_key": f"step-{child_id}",
        "step_index": child_id,
        "wave_index": 0,
        "worktree_path": None,
        "branch_name": None,
        "base_commit_hash": "base",
        "result_commit_hash": None,
        "merge_status": "pending",
        "verification_result_json": None,
        "result_summary": None,
        "error_message": None,
        "started_at": None,
        "finished_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_cancel_revokes_root_and_children_and_persists_soft_barrier(
    monkeypatch,
):
    parent = _parent(TaskStatus.RUNNING, {"canvas_id": "canvas-root"})
    children = [
        _child(1, TaskStatus.RUNNING, celery_task_id="step-running"),
        _child(2, TaskStatus.PENDING),
        _child(3, TaskStatus.SUCCESS),
    ]
    db = FakeSession(parent, children)
    revoked = []
    cleaned = []
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service.celery_app.control,
        "revoke",
        lambda task_id, terminate: revoked.append((task_id, terminate)),
    )
    monkeypatch.setattr(
        service,
        "_cleanup_safe_step_worktrees",
        lambda parent_id: cleaned.append(parent_id),
    )

    result = service.cancel_orchestrator(parent.id)

    assert result["status"] == "cancelled"
    assert {item[0] for item in revoked} == {
        "canvas-root",
        "resume-task",
        "step-running",
    }
    assert parent.status == TaskStatus.CANCELLED
    assert children[0].status == TaskStatus.CANCELLED
    assert children[1].status == TaskStatus.CANCELLED
    assert children[2].status == TaskStatus.SUCCESS
    assert db.commits == 1
    assert cleaned == [100]


def test_retry_preserves_merged_steps_and_starts_first_incomplete_wave(
    monkeypatch,
):
    parent = _parent(
        TaskStatus.FAILED,
        {
            "canvas_id": "old-canvas",
            "execution_waves": [
                {"index": 0, "step_ids": ["step-1"]},
                {"index": 1, "step_ids": ["step-2"]},
            ],
        },
    )
    merged = _child(
        1,
        TaskStatus.SUCCESS,
        wave_index=0,
        merge_status="merged",
        result_commit_hash="commit-1",
    )
    failed = _child(
        2,
        TaskStatus.FAILED,
        wave_index=1,
        merge_status="conflict",
        result_commit_hash="commit-2",
        error_message="conflict",
    )
    db = FakeSession(parent, [merged, failed])
    dispatched = []
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        orchestrator_dispatch_service,
        "dispatch_orchestrator_execution",
        lambda parent_id, start_wave_index=0: dispatched.append(
            (parent_id, start_wave_index)
        )
        or {"status": "dispatched", "canvas_id": "new-canvas"},
    )

    canvas_id = service.retry_failed_orchestrator(parent.id)

    assert canvas_id == "new-canvas"
    assert merged.status == TaskStatus.SUCCESS
    assert merged.result_commit_hash == "commit-1"
    assert failed.status == TaskStatus.PENDING
    assert failed.result_commit_hash is None
    assert failed.merge_status == "pending"
    assert failed.error_message is None
    assert dispatched == [(100, 1)]


def test_retry_persists_dispatch_failure(monkeypatch):
    parent = _parent(TaskStatus.FAILED, {"canvas_id": "old-canvas"})
    failed = _child(1, TaskStatus.FAILED, error_message="agent failed")
    db = FakeSession(parent, [failed])
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        orchestrator_dispatch_service,
        "dispatch_orchestrator_execution",
        lambda parent_id, start_wave_index=0: {
            "status": "failed",
            "error": "broker unavailable",
        },
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        service.retry_failed_orchestrator(parent.id)

    assert parent.status == TaskStatus.FAILED
    assert parent.error_message == "broker unavailable"
    assert json.loads(parent.metadata_json)["plan_status"] == (
        "retry_dispatch_failed"
    )
    assert db.commits == 2


def test_cancel_persists_cleanup_failure(monkeypatch):
    parent = _parent(TaskStatus.RUNNING, {})
    db = FakeSession(parent)
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_cleanup_safe_step_worktrees",
        lambda parent_id: (_ for _ in ()).throw(
            RuntimeError("worktree cleanup failed")
        ),
    )

    result = service.cancel_orchestrator(parent.id)

    assert result["status"] == "cancelled"
    assert result["cleanup_error"] == "worktree cleanup failed"
    assert parent.status == TaskStatus.CANCELLED
    assert parent.error_message == "worktree cleanup failed"
    assert db.commits == 2


def test_reconcile_recreates_missing_step_from_recorded_base(monkeypatch):
    parent = _parent(
        TaskStatus.RUNNING,
        {
            "integration_worktree_path": "integration",
            "integration_branch_name": "agent/orchestrator-100/integration",
        },
    )
    child = _child(
        1,
        TaskStatus.PENDING,
        worktree_path="missing-step",
        branch_name="agent/orchestrator-100/step-1",
    )
    db = FakeSession(parent, [child])
    actions = []

    class Worktrees:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def abort_cherry_pick(self, path):
            return False

        def worktree_exists(self, path):
            return False

        def ensure_step_worktree(self, parent_id, step_key, base):
            actions.append((parent_id, step_key, base))
            return SimpleNamespace(
                path="rebuilt-step",
                branch_name="agent/orchestrator-100/step-1",
            )

        def commit_is_ancestor(self, commit, revision):
            return False

        def prune(self):
            return None

    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(service, "_repository", lambda db, parent: object())
    monkeypatch.setattr(service, "_service", lambda repository: Worktrees())
    monkeypatch.setattr(service, "_broadcast_recovery_logs", lambda *args: None)

    result = service.reconcile_orchestrator(parent.id)

    assert result["status"] == "reconciled"
    assert actions == [(100, "step-1", "base")]
    assert child.worktree_path == "rebuilt-step"
    assert any("recreated" in item for item in result["actions"])


def test_cleanup_preserves_integration_branch_for_generated_code_change(
    monkeypatch,
):
    parent = _parent(
        TaskStatus.SUCCESS,
        {
            "code_change_id": 99,
            "integration_worktree_path": "integration",
            "integration_branch_name": "agent/orchestrator-100/integration",
        },
    )
    code_change = SimpleNamespace(status=CodeChangeStatus.GENERATED)
    db = FakeSession(parent, [], code_change)
    removed = []

    class Worktrees:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def prune(self):
            return None

        def remove_worktree(self, path):
            removed.append(path)

        def cleanup_step_branch(self, branch):
            removed.append(branch)

        def cleanup_integration_branch(self, path, branch):
            removed.append((path, branch))

    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(service, "_repository", lambda db, parent: object())
    monkeypatch.setattr(service, "_service", lambda repository: Worktrees())

    result = service.cleanup_terminal_orchestrator(parent.id, force=True)

    assert result["status"] == "cleaned"
    assert result["integration_preserved"] is True
    assert ("integration", "agent/orchestrator-100/integration") not in removed
