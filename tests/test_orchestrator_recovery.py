import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.models.code_change import CodeChange
from app.models.task import Task
from app.schemas.enums import CodeChangeStatus, TaskStatus
from app.services import orchestrator_dispatch_service
from app.services import orchestrator_recovery_service as service
from app.services import task_service


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

    def scalar(self, statement):
        return self.parent

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


def test_cancel_locks_and_refreshes_latest_parent_before_transition(
    monkeypatch,
):
    stale_parent = _parent(
        TaskStatus.RUNNING,
        {
            "canvas_id": "stale-canvas",
            "execution_generation": 1,
        },
    )
    latest_parent = _parent(
        TaskStatus.RUNNING,
        {
            "canvas_id": "latest-canvas",
            "canvas_task_ids": ["latest-step"],
            "execution_generation": 7,
        },
    )

    class StaleIdentityMapSession(FakeSession):
        def get(self, model, object_id):
            if model is Task:
                return stale_parent
            return super().get(model, object_id)

        def scalar(self, statement):
            assert statement._for_update_arg is not None
            assert statement.get_execution_options()["populate_existing"] is True
            return latest_parent

    db = StaleIdentityMapSession(latest_parent)
    revoked = []
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service.celery_app.control,
        "revoke",
        lambda task_id, terminate: revoked.append(task_id),
    )
    monkeypatch.setattr(service, "_cleanup_safe_step_worktrees", lambda _: None)

    result = service.cancel_orchestrator(latest_parent.id)

    assert result["status"] == "cancelled"
    assert set(revoked) == {"latest-canvas", "latest-step", "resume-task"}
    assert latest_parent.status == TaskStatus.CANCELLED
    assert json.loads(latest_parent.metadata_json)["execution_generation"] == 8
    assert stale_parent.status == TaskStatus.RUNNING
    assert db.commits == 1


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
    assert json.loads(parent.error_message) == {
        "cleanup_error": "worktree cleanup failed",
        "failed_revocations": {},
    }
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


def test_retry_keeps_skipped_steps_and_starts_at_failed_later_wave(monkeypatch):
    parent = _parent(TaskStatus.FAILED, {"canvas_id": "old"})
    skipped = _child(
        1,
        TaskStatus.SUCCESS,
        wave_index=0,
        merge_status="skipped",
    )
    failed = _child(2, TaskStatus.FAILED, wave_index=1)
    db = FakeSession(parent, [skipped, failed])
    calls = []
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        orchestrator_dispatch_service,
        "dispatch_orchestrator_execution",
        lambda parent_id, start_wave_index=0: calls.append(start_wave_index)
        or {"status": "dispatched", "canvas_id": "retry"},
    )

    service.retry_failed_orchestrator(parent.id)

    assert skipped.status == TaskStatus.SUCCESS
    assert skipped.merge_status == "skipped"
    assert calls == [1]


def test_cancel_is_terminal_safe_and_revokes_each_id(monkeypatch):
    parent = _parent(
        TaskStatus.RUNNING,
        {"canvas_task_ids": ["canvas-a", "canvas-b"]},
    )
    child = _child(1, TaskStatus.RUNNING, celery_task_id="child")
    db = FakeSession(parent, [child])
    calls = []
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service.celery_app.control,
        "revoke",
        lambda task_id, terminate: calls.append(task_id)
        if task_id != "canvas-a"
        else (_ for _ in ()).throw(RuntimeError("revoke failed")),
    )
    monkeypatch.setattr(service, "_cleanup_safe_step_worktrees", lambda _: None)

    result = service.cancel_orchestrator(parent.id)

    assert set(calls) == {"canvas-b", "resume-task", "child"}
    assert result["failed_revocations"] == {"canvas-a": "revoke failed"}
    assert parent.status == TaskStatus.CANCELLED

    result = service.cancel_orchestrator(parent.id)
    assert result["status"] == "conflict"
    assert parent.status == TaskStatus.CANCELLED


def test_reconcile_recreates_missing_integration_worktree(monkeypatch):
    parent = _parent(
        TaskStatus.RUNNING,
        {
            "integration_worktree_path": "missing-integration",
            "integration_branch_name": "agent/orchestrator-100/integration",
            "base_commit_hash": "base",
        },
    )
    db = FakeSession(parent)
    actions = []

    class Worktrees:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def worktree_exists(self, path): return False
        def ensure_integration_worktree(self, parent_id, base):
            actions.append((parent_id, base))
            return SimpleNamespace(path="rebuilt-integration", branch_name="agent/orchestrator-100/integration")
        def abort_cherry_pick(self, path): return False
        def prune(self): return None

    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(service, "_repository", lambda *_: object())
    monkeypatch.setattr(service, "_service", lambda _: Worktrees())
    monkeypatch.setattr(service, "_broadcast_recovery_logs", lambda *_: None)

    result = service.reconcile_orchestrator(parent.id)

    assert actions == [(100, "base")]
    assert json.loads(parent.metadata_json)["integration_worktree_path"] == "rebuilt-integration"
    assert any("integration" in action for action in result["actions"])


def test_cleanup_preserves_failed_diagnostics_without_force(monkeypatch):
    parent = _parent(TaskStatus.FAILED, {})
    child = _child(1, TaskStatus.FAILED, worktree_path="step", branch_name="branch")
    db = FakeSession(parent, [child])
    removed = []
    class Worktrees:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def remove_worktree(self, path): removed.append(path)
        def cleanup_step_branch(self, branch): removed.append(branch)
        def prune(self): removed.append("prune")
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(service, "_repository", lambda *_: object())
    monkeypatch.setattr(service, "_service", lambda _: Worktrees())

    assert service.cleanup_terminal_orchestrator(parent.id) == {
        "status": "preserved", "reason": "terminal diagnostics are retained"
    }
    assert removed == []


def test_only_valid_execution_phase_plan_is_recoverable():
    task = SimpleNamespace(
        metadata_json=json.dumps({
            "plan_status": "awaiting_confirmation",
            "plan": [{"id": "step", "agent": "backend", "instruction": "x", "depends_on": [], "write_scope": ["app/**"]}],
        }),
        agent=SimpleNamespace(adapter_type="langgraph"),
    )
    assert task_service.is_orchestrator_task(task) is False
    task.metadata_json = json.dumps({
        "plan_status": "dispatch_queued",
        "plan": [{"id": "step", "agent": "backend", "instruction": "x", "depends_on": [], "write_scope": ["app/**"]}],
    })
    assert task_service.is_orchestrator_task(task) is True


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ValueError("not retryable"), 409),
        (RuntimeError("broker unavailable"), 503),
    ],
)
def test_in_place_orchestrator_retry_maps_service_errors(
    monkeypatch,
    error,
    expected_status,
):
    import asyncio

    from fastapi import HTTPException, Response

    from app.api import tasks as tasks_api

    task = SimpleNamespace(id=100, parent_task_id=None)
    db = SimpleNamespace(expire_all=lambda: None)
    monkeypatch.setattr(tasks_api, "get_owned_task", lambda *_: task)
    monkeypatch.setattr(
        tasks_api.task_service,
        "ensure_user_task_capacity",
        lambda *_: None,
    )
    monkeypatch.setattr(
        tasks_api.task_service,
        "is_orchestrator_task",
        lambda _: True,
    )
    monkeypatch.setattr(
        tasks_api.orchestrator_recovery_service,
        "retry_failed_orchestrator",
        lambda _: (_ for _ in ()).throw(error),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            tasks_api.retry_task(
                100,
                response=Response(status_code=201),
                current_user=SimpleNamespace(id=1),
                db=db,
            )
        )

    assert raised.value.status_code == expected_status


def test_in_place_orchestrator_retry_returns_accepted_without_changing_normal_default(
    monkeypatch,
):
    import asyncio

    from fastapi import Response

    from app.api import tasks as tasks_api

    task = SimpleNamespace(id=100, parent_task_id=None)
    db = SimpleNamespace(expire_all=lambda: None)
    response = Response(status_code=201)
    monkeypatch.setattr(tasks_api, "get_owned_task", lambda *_: task)
    monkeypatch.setattr(
        tasks_api.task_service,
        "ensure_user_task_capacity",
        lambda *_: None,
    )
    monkeypatch.setattr(
        tasks_api.task_service,
        "is_orchestrator_task",
        lambda _: True,
    )
    monkeypatch.setattr(
        tasks_api.orchestrator_recovery_service,
        "retry_failed_orchestrator",
        lambda _: "retry-canvas",
    )

    async def noop_broadcast(*_):
        return None

    monkeypatch.setattr(
        tasks_api.task_service,
        "broadcast_task_event",
        noop_broadcast,
    )

    retried = asyncio.run(
        tasks_api.retry_task(
            100,
            response=response,
            current_user=SimpleNamespace(id=1),
            db=db,
        )
    )

    assert retried is task
    assert response.status_code == 202
    assert tasks_api.router.routes[
        next(
            index
            for index, route in enumerate(tasks_api.router.routes)
            if getattr(route, "path", "") == "/{task_id}/retry"
        )
    ].status_code == 201


def test_regular_retry_still_creates_and_dispatches_a_new_task(
    monkeypatch,
):
    import asyncio

    from fastapi import Response

    from app.api import tasks as tasks_api

    task = SimpleNamespace(
        id=100,
        parent_task_id=50,
        agent=SimpleNamespace(adapter_type="mock"),
    )
    retried = SimpleNamespace(
        id=101,
        agent=SimpleNamespace(adapter_type="mock"),
        celery_task_id=None,
    )
    commits = []
    events = []
    db = SimpleNamespace(
        commit=lambda: commits.append("commit"),
        refresh=lambda value: None,
    )
    response = Response(status_code=201)
    monkeypatch.setattr(tasks_api, "get_owned_task", lambda *_: task)
    monkeypatch.setattr(
        tasks_api.task_service,
        "ensure_user_task_capacity",
        lambda *_: None,
    )
    monkeypatch.setattr(
        tasks_api.task_service,
        "create_retry_task",
        lambda *_: retried,
    )
    monkeypatch.setattr(
        tasks_api.agent_tasks.run_agent_task,
        "delay",
        lambda *_: SimpleNamespace(id="regular-retry-celery"),
    )

    async def record_event(value, name):
        events.append((value.id, name))

    monkeypatch.setattr(
        tasks_api.task_service,
        "broadcast_task_event",
        record_event,
    )

    result = asyncio.run(
        tasks_api.retry_task(
            100,
            response=response,
            current_user=SimpleNamespace(id=1),
            db=db,
        )
    )

    assert result is retried
    assert retried.celery_task_id == "regular-retry-celery"
    assert response.status_code == 201
    assert commits == ["commit"]
    assert events == [(101, "task.created"), (101, "task.updated")]
