import json
from types import SimpleNamespace

from app.schemas.enums import TaskStatus
from app.services import orchestrator_execution_service as service


class FakeSession:
    def __init__(self, objects):
        self.objects = objects
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def get(self, model, object_id):
        return self.objects.get(object_id)

    def scalar(self, statement):
        params = statement.compile().params
        return self.objects.get(next(iter(params.values())))

    def scalars(self, statement):
        return [
            value
            for value in self.objects.values()
            if getattr(value, "parent_task_id", None) is not None
        ]

    def commit(self):
        self.commits += 1

    def add(self, value):
        return None

    def refresh(self, value):
        return None


def test_prepare_execution_records_invalid_plan_failure(monkeypatch):
    parent = SimpleNamespace(
        id=10,
        metadata_json=json.dumps({"plan": []}),
        status=TaskStatus.RUNNING,
        error_message=None,
    )
    db = FakeSession({10: parent})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)

    result = service.prepare_execution(10)

    assert result["status"] == "failed"
    assert parent.status == TaskStatus.FAILED
    assert parent.error_message
    assert json.loads(parent.metadata_json)["plan_status"] == "prepare_failed"
    assert db.commits == 1


def test_execute_step_business_failure_is_persisted_and_structured(monkeypatch):
    parent = SimpleNamespace(
        id=10,
        status=TaskStatus.RUNNING,
        metadata_json=json.dumps({"execution_generation": 1}),
    )
    child = SimpleNamespace(
        id=11,
        parent_task_id=10,
        status=TaskStatus.PENDING,
        metadata_json=json.dumps({"execution_generation": 1}),
        result_commit_hash=None,
        celery_task_id=None,
        started_at=None,
        finished_at=None,
        error_message=None,
    )
    db = FakeSession({10: parent, 11: child})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_repository",
        lambda db, task: (_ for _ in ()).throw(
            RuntimeError("repository unavailable")
        ),
    )

    outcome = service.execute_step(11, "celery-11")

    assert outcome.status == "FAILED"
    assert outcome.error == "repository unavailable"
    assert child.status == TaskStatus.FAILED
    assert child.error_message == "repository unavailable"
    assert child.celery_task_id == "celery-11"
    assert db.commits == 2


def test_execute_step_old_generation_is_skipped_before_claim_side_effects(
    monkeypatch,
):
    parent = SimpleNamespace(
        id=10,
        status=TaskStatus.RUNNING,
        metadata_json=json.dumps({"execution_generation": 2}),
    )
    child = SimpleNamespace(
        id=11,
        parent_task_id=10,
        status=TaskStatus.PENDING,
        metadata_json=json.dumps({"execution_generation": 2}),
        result_commit_hash=None,
        celery_task_id=None,
        started_at=None,
    )
    db = FakeSession({10: parent, 11: child})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_repository",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("stale delivery reached repository side effects")
        ),
    )

    outcome = service.execute_step(11, "old-step-id", 1)

    assert outcome.status == "SKIPPED"
    assert child.status == TaskStatus.PENDING
    assert child.celery_task_id is None
    assert child.started_at is None
    assert db.commits == 0


def test_execute_step_duplicate_running_delivery_does_not_repeat_agent(
    monkeypatch,
):
    parent = SimpleNamespace(
        id=10,
        status=TaskStatus.RUNNING,
        metadata_json=json.dumps({"execution_generation": 3}),
    )
    child = SimpleNamespace(
        id=11,
        parent_task_id=10,
        status=TaskStatus.RUNNING,
        metadata_json=json.dumps({"execution_generation": 3}),
        result_commit_hash=None,
        celery_task_id="stable-step-id",
        started_at=service._utcnow(),
    )
    db = FakeSession({10: parent, 11: child})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_repository",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("duplicate delivery repeated Agent execution")
        ),
    )

    outcome = service.execute_step(11, "stable-step-id", 3)

    assert outcome.status == "SKIPPED"
    assert child.status == TaskStatus.RUNNING
    assert child.celery_task_id == "stable-step-id"
    assert db.commits == 0


def test_execute_step_rejects_different_task_id_in_same_generation(
    monkeypatch,
):
    parent = SimpleNamespace(
        id=10,
        status=TaskStatus.RUNNING,
        metadata_json=json.dumps({"execution_generation": 3}),
    )
    child = SimpleNamespace(
        id=11,
        parent_task_id=10,
        status=TaskStatus.RUNNING,
        metadata_json=json.dumps({"execution_generation": 3}),
        result_commit_hash=None,
        celery_task_id="claimed-step-id",
        started_at=service._utcnow(),
    )
    db = FakeSession({10: parent, 11: child})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_repository",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("same-generation replacement repeated Agent")
        ),
    )

    outcome = service.execute_step(11, "replacement-step-id", 3)

    assert outcome.status == "SKIPPED"
    assert child.celery_task_id == "claimed-step-id"
    assert db.commits == 0


def test_finalizer_is_idempotent_after_code_change(monkeypatch):
    parent = SimpleNamespace(
        id=10,
        metadata_json=json.dumps({"code_change_id": 77}),
        status=TaskStatus.RUNNING,
        error_message=None,
    )
    db = FakeSession({10: parent})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)

    first = service.finalize_execution(10)
    second = service.finalize_execution(10)

    assert first == second == {"status": "success", "code_change_id": 77}
    assert db.commits == 0


def test_utcnow_is_timezone_aware():
    assert service._utcnow().utcoffset() is not None


def test_prepare_execution_treats_cancelled_parent_as_terminal_barrier(
    monkeypatch,
):
    parent = SimpleNamespace(
        id=10,
        metadata_json=json.dumps(
            {
                "execution_generation": 2,
                "plan": [
                    {
                        "id": "backend",
                        "agent": "backend",
                        "instruction": "Implement backend",
                        "depends_on": [],
                        "write_scope": ["app/**"],
                    }
                ],
            }
        ),
        status=TaskStatus.CANCELLED,
        error_message=None,
    )
    db = FakeSession({10: parent})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_repository",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("terminal callback reached repository")
        ),
    )

    result = service.prepare_execution(10, 2)

    assert result == {"status": "skipped", "reason": "parent is terminal"}
    assert db.commits == 0


def _running_parent_for_barrier(metadata):
    return SimpleNamespace(
        id=10,
        metadata_json=json.dumps(
            {"execution_generation": 1, **metadata}
        ),
        status=TaskStatus.RUNNING,
        error_message=None,
        instruction="Implement backend",
    )


def _cancel_after_callback_entry(parent):
    metadata = json.loads(parent.metadata_json)
    metadata["execution_generation"] = 2
    parent.metadata_json = json.dumps(metadata)
    parent.status = TaskStatus.CANCELLED
    return SimpleNamespace(id=1, user_id=1, local_path="repo")


def test_prepare_execution_skips_when_cancel_wins_after_callback_entry(
    monkeypatch,
):
    parent = _running_parent_for_barrier(
        {
            "plan": [
                {
                    "id": "backend",
                    "agent": "backend",
                    "instruction": "Implement backend",
                    "depends_on": [],
                    "write_scope": ["app/**"],
                }
            ]
        }
    )
    child = SimpleNamespace(
        id=11,
        parent_task_id=10,
        step_key="backend",
    )
    db = FakeSession({10: parent, 11: child})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_repository",
        lambda *_: _cancel_after_callback_entry(parent),
    )
    monkeypatch.setattr(
        service,
        "_service",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("stale callback reached worktree side effect")
        ),
    )

    result = service.prepare_execution(10, 1)

    assert result == {"status": "skipped", "reason": "stale execution"}
    assert db.commits == 0


def test_prepare_wave_skips_when_retry_wins_after_callback_entry(
    monkeypatch,
):
    parent = _running_parent_for_barrier(
        {
            "integration_branch_name": "agent/orchestrator-10/integration",
        }
    )
    child = SimpleNamespace(
        id=11,
        parent_task_id=10,
        wave_index=0,
        status=TaskStatus.PENDING,
    )
    db = FakeSession({10: parent, 11: child})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_repository",
        lambda *_: _cancel_after_callback_entry(parent),
    )
    monkeypatch.setattr(
        service,
        "_service",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("stale callback reached worktree side effect")
        ),
    )

    result = service.prepare_wave(10, 0, 1)

    assert result == {"status": "skipped", "reason": "stale execution"}
    assert db.commits == 0


def test_merge_wave_skips_when_cancel_wins_after_callback_entry(
    monkeypatch,
):
    parent = _running_parent_for_barrier(
        {
            "integration_worktree_path": "integration",
            "integration_branch_name": "agent/orchestrator-10/integration",
            "base_commit_hash": "base",
        }
    )
    child = SimpleNamespace(
        id=11,
        parent_task_id=10,
        wave_index=0,
        status=TaskStatus.SUCCESS,
        step_index=0,
        merge_status="ready",
        result_commit_hash="commit",
    )
    db = FakeSession({10: parent, 11: child})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_repository",
        lambda *_: _cancel_after_callback_entry(parent),
    )
    monkeypatch.setattr(
        service,
        "_service",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("stale callback reached worktree side effect")
        ),
    )

    result = service.merge_wave(10, 0, 1)

    assert result == {"status": "skipped", "reason": "stale execution"}
    assert db.commits == 0


def test_finalize_skips_when_retry_wins_after_callback_entry(
    monkeypatch,
):
    parent = _running_parent_for_barrier(
        {
            "integration_worktree_path": "integration",
            "integration_branch_name": "agent/orchestrator-10/integration",
            "base_commit_hash": "base",
        }
    )
    db = FakeSession({10: parent})
    monkeypatch.setattr(service, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        service,
        "_repository",
        lambda *_: _cancel_after_callback_entry(parent),
    )
    monkeypatch.setattr(
        service,
        "_service",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("stale callback reached worktree side effect")
        ),
    )

    result = service.finalize_execution(10, 1)

    assert result == {"status": "skipped", "reason": "stale execution"}
    assert db.commits == 0
