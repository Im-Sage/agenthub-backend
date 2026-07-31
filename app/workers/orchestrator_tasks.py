from dataclasses import asdict

from app.core.config import settings
from app.services import orchestrator_execution_service as execution_service
from app.workers.celery_app import celery_app


@celery_app.task(
    name=(
        "app.workers.orchestrator_tasks."
        "prepare_orchestrator_execution"
    )
)
def prepare_orchestrator_execution(parent_task_id: int, execution_generation: int = 0) -> dict:
    if execution_generation == 0:
        return execution_service.prepare_execution(parent_task_id)
    return execution_service.prepare_execution(parent_task_id, execution_generation)


@celery_app.task(
    name="app.workers.orchestrator_tasks.prepare_orchestrator_wave"
)
def prepare_orchestrator_wave(
    parent_task_id: int,
    wave_index: int, execution_generation: int = 0,
) -> dict:
    if execution_generation == 0:
        return execution_service.prepare_wave(parent_task_id, wave_index)
    return execution_service.prepare_wave(parent_task_id, wave_index, execution_generation)


@celery_app.task(
    bind=True,
    name="app.workers.orchestrator_tasks.run_orchestrator_step",
    max_retries=settings.orchestrator_step_max_retries,
)
def run_orchestrator_step(self, child_task_id: int, execution_generation: int = 0) -> dict:
    if execution_generation == 0:
        outcome = execution_service.execute_step(child_task_id, str(self.request.id))
    else:
        outcome = execution_service.execute_step(child_task_id, str(self.request.id), execution_generation)
    result = asdict(outcome)
    result["changed_files"] = list(outcome.changed_files)
    return result


@celery_app.task(
    name="app.workers.orchestrator_tasks.merge_orchestrator_wave"
)
def merge_orchestrator_wave(
    parent_task_id: int,
    wave_index: int, execution_generation: int = 0,
) -> dict:
    if execution_generation == 0:
        return execution_service.merge_wave(parent_task_id, wave_index)
    return execution_service.merge_wave(parent_task_id, wave_index, execution_generation)


@celery_app.task(
    name=(
        "app.workers.orchestrator_tasks."
        "finalize_orchestrator_execution"
    )
)
def finalize_orchestrator_execution(parent_task_id: int, execution_generation: int = 0) -> dict:
    if execution_generation == 0:
        return execution_service.finalize_execution(parent_task_id)
    return execution_service.finalize_execution(parent_task_id, execution_generation)


@celery_app.task(
    name="app.workers.orchestrator_tasks.cleanup_orchestrator_worktrees"
)
def cleanup_orchestrator_worktrees(
    parent_task_id: int,
    force: bool = False,
) -> dict:
    from app.services.orchestrator_recovery_service import (
        cleanup_terminal_orchestrator,
    )

    return cleanup_terminal_orchestrator(
        parent_task_id,
        force=force,
    )
