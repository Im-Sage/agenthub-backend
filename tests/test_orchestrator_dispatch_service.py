import json
from types import SimpleNamespace

from app.services.orchestrator_execution_service import StepExecutionOutcome
from app.schemas.enums import TaskStatus
from app.services import orchestrator_dispatch_service as service


class FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class FakeSession:
    def __init__(self, parent, children=()):
        self.parent = parent
        self.children = list(children)
        self.commits = 0
        self.commit_metadata = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def scalar(self, statement):
        return self.parent

    def scalars(self, statement):
        return FakeScalars(self.children)

    def commit(self):
        self.commits += 1
        self.commit_metadata.append(json.loads(self.parent.metadata_json))


def _parent(metadata):
    return SimpleNamespace(
        id=100,
        status=TaskStatus.PENDING,
        error_message=None,
        metadata_json=json.dumps(metadata),
    )


def test_canvas_uses_only_immutable_signatures_and_always_ends_in_finalizer():
    children = [
        SimpleNamespace(id=11, step_key="backend"),
        SimpleNamespace(id=12, step_key="tests"),
    ]
    waves = [
        {"index": 0, "step_ids": ["backend"]},
        {"index": 1, "step_ids": ["tests"]},
    ]

    canvas = service.build_orchestrator_canvas(100, children, waves)
    serialized = canvas.__json__()

    def signatures(value):
        if isinstance(value, dict):
            if "task" in value and "immutable" in value:
                yield value
            for nested in value.values():
                yield from signatures(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from signatures(nested)

    all_signatures = [
        item
        for item in signatures(serialized)
        if item["task"].startswith("app.workers.orchestrator_tasks.")
    ]
    assert all_signatures
    assert all(item["immutable"] is True for item in all_signatures)
    rendered = str(canvas)
    assert "finalize_orchestrator_execution(100)" in rendered
    assert rendered.rfind("finalize_orchestrator_execution") > rendered.rfind(
        "merge_orchestrator_wave"
    )


def test_business_failure_still_reaches_finalizer(monkeypatch):
    from app.workers import orchestrator_tasks

    calls = []
    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "prepare_execution",
        lambda parent_id: {"status": "prepared"},
    )
    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "prepare_wave",
        lambda parent_id, wave_index: {"status": "prepared"},
    )
    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "execute_step",
        lambda child_id, celery_id: StepExecutionOutcome(
            child_id,
            "FAILED",
            None,
            (),
            None,
            "agent failed",
        ),
    )
    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "merge_wave",
        lambda parent_id, wave_index: {
            "status": "failed",
            "error": "wave failed",
        },
    )

    def finalize(parent_id):
        calls.append(parent_id)
        return {"status": "failed", "error": "wave failed"}

    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "finalize_execution",
        finalize,
    )
    canvas = service.build_orchestrator_canvas(
        100,
        [SimpleNamespace(id=11, step_key="backend")],
        [{"index": 0, "step_ids": ["backend"]}],
    )

    result = canvas.apply().get()

    assert result["status"] == "failed"
    assert calls == [100]


def test_dispatch_is_idempotent_when_canvas_id_exists(monkeypatch):
    parent = _parent(
        {
            "canvas_id": "existing-root",
            "plan_status": "dispatch_queued",
        }
    )
    db = FakeSession(parent)
    monkeypatch.setattr(service, "SessionLocal", lambda: db)

    result = service.dispatch_orchestrator_execution(parent.id)

    assert result == {
        "status": "dispatched",
        "canvas_id": "existing-root",
    }
    assert db.commits == 0


def test_enqueue_failure_is_persisted(monkeypatch):
    parent = _parent(
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
    child = SimpleNamespace(id=11, step_key="backend")
    db = FakeSession(parent, [child])
    monkeypatch.setattr(service, "SessionLocal", lambda: db)

    class BrokenCanvas:
        def apply_async(self):
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        service,
        "build_orchestrator_canvas",
        lambda *args, **kwargs: BrokenCanvas(),
    )

    result = service.dispatch_orchestrator_execution(parent.id)

    assert result == {
        "status": "failed",
        "error": "broker unavailable",
    }
    assert parent.status == TaskStatus.FAILED
    assert parent.error_message == "broker unavailable"
    assert json.loads(parent.metadata_json)["plan_status"] == "dispatch_failed"
    assert db.commits == 2


def test_dispatch_persists_prepared_ids_before_publish_then_marks_queued(
    monkeypatch,
):
    parent = _parent(
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
    child = SimpleNamespace(id=11, step_key="backend")
    db = FakeSession(parent, [child])
    published = []
    monkeypatch.setattr(service, "SessionLocal", lambda: db)

    class Canvas:
        _orchestrator_task_ids = ["prepare", "wave", "step", "merge", "final"]

        def apply_async(self):
            published.append(json.loads(parent.metadata_json))

    monkeypatch.setattr(
        service,
        "build_orchestrator_canvas",
        lambda *args, **kwargs: Canvas(),
    )

    result = service.dispatch_orchestrator_execution(parent.id)

    assert result["status"] == "dispatched"
    assert published[0]["plan_status"] == "dispatch_prepared"
    assert published[0]["canvas_task_ids"] == Canvas._orchestrator_task_ids
    assert [item["plan_status"] for item in db.commit_metadata] == [
        "dispatch_prepared",
        "dispatch_queued",
    ]


def test_prepared_dispatch_republishes_with_same_stable_ids(monkeypatch):
    stable_ids = ["prepare", "wave", "step", "merge", "final"]
    parent = _parent(
        {
            "plan": [
                {
                    "id": "backend",
                    "agent": "backend",
                    "instruction": "Implement backend",
                    "depends_on": [],
                    "write_scope": ["app/**"],
                }
            ],
            "execution_generation": 3,
            "execution_id": "execution-stable",
            "canvas_id": "canvas-stable",
            "canvas_task_ids": stable_ids,
            "plan_status": "dispatch_prepared",
        }
    )
    child = SimpleNamespace(id=11, step_key="backend")
    db = FakeSession(parent, [child])
    observed = []
    monkeypatch.setattr(service, "SessionLocal", lambda: db)

    class Canvas:
        _orchestrator_task_ids = stable_ids

        def apply_async(self):
            observed.append("published")

    def build(*args, **kwargs):
        observed.append(kwargs.get("task_ids"))
        return Canvas()

    monkeypatch.setattr(service, "build_orchestrator_canvas", build)

    result = service.dispatch_orchestrator_execution(parent.id)

    assert result == {
        "status": "dispatched",
        "canvas_id": "canvas-stable",
    }
    assert observed == [stable_ids, "published"]
    assert json.loads(parent.metadata_json)["plan_status"] == "dispatch_queued"


def test_publish_failure_does_not_overwrite_concurrent_cancellation(
    monkeypatch,
):
    parent = _parent(
        {
            "plan": [
                {
                    "id": "backend",
                    "agent": "backend",
                    "instruction": "Implement backend",
                    "depends_on": [],
                    "write_scope": ["app/**"],
                }
            ],
            "execution_generation": 4,
        }
    )
    child = SimpleNamespace(id=11, step_key="backend")
    db = FakeSession(parent, [child])
    monkeypatch.setattr(service, "SessionLocal", lambda: db)

    class CancelledDuringPublish:
        _orchestrator_task_ids = ["prepare", "wave", "step", "merge", "final"]

        def apply_async(self):
            metadata = json.loads(parent.metadata_json)
            metadata["execution_generation"] = 5
            metadata["plan_status"] = "cancelled"
            parent.metadata_json = json.dumps(metadata)
            parent.status = TaskStatus.CANCELLED
            parent.error_message = "cancel requested"
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        service,
        "build_orchestrator_canvas",
        lambda *args, **kwargs: CancelledDuringPublish(),
    )

    result = service.dispatch_orchestrator_execution(parent.id)

    metadata = json.loads(parent.metadata_json)
    assert result == {"status": "failed", "error": "broker unavailable"}
    assert parent.status == TaskStatus.CANCELLED
    assert parent.error_message == "cancel requested"
    assert metadata["execution_generation"] == 5
    assert metadata["plan_status"] == "cancelled"
    assert metadata["dispatch_errors"] == [
        {
            "stage": "publish",
            "execution_generation": 4,
            "error": "broker unavailable",
        }
    ]
