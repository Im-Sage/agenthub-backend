from app.services.orchestrator_execution_service import StepExecutionOutcome


def test_worker_registers_orchestrator_module_and_reliable_delivery_options():
    from app.workers.celery_app import celery_app

    assert "app.workers.orchestrator_tasks" in celery_app.conf.include
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.task_track_started is True
    assert celery_app.conf.result_expires == 86400


def test_bound_step_passes_celery_request_id_and_returns_json(monkeypatch):
    from app.workers import orchestrator_tasks

    observed = {}

    def execute(child_task_id, celery_task_id):
        observed.update(
            child_task_id=child_task_id,
            celery_task_id=celery_task_id,
        )
        return StepExecutionOutcome(
            child_task_id=child_task_id,
            status="FAILED",
            commit_hash=None,
            changed_files=(),
            verification_result=None,
            error="business failure",
        )

    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "execute_step",
        execute,
    )

    result = orchestrator_tasks.run_orchestrator_step.apply(
        args=(42,),
        task_id="celery-step-42",
    ).get()

    assert observed == {
        "child_task_id": 42,
        "celery_task_id": "celery-step-42",
    }
    assert result == {
        "child_task_id": 42,
        "status": "FAILED",
        "commit_hash": None,
        "changed_files": [],
        "verification_result": None,
        "error": "business failure",
    }


def test_finalizer_task_returns_business_failure_instead_of_raising(
    monkeypatch,
):
    from app.workers import orchestrator_tasks

    monkeypatch.setattr(
        orchestrator_tasks.execution_service,
        "finalize_execution",
        lambda parent_task_id: {
            "status": "failed",
            "error": "verification failed",
        },
    )

    result = orchestrator_tasks.finalize_orchestrator_execution.apply(
        args=(7,),
    ).get()

    assert result["status"] == "failed"

