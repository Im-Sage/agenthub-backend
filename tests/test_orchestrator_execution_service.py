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
    parent = SimpleNamespace(id=10, status=TaskStatus.RUNNING)
    child = SimpleNamespace(
        id=11,
        parent_task_id=10,
        status=TaskStatus.PENDING,
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
