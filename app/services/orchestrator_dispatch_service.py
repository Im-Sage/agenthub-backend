import json
from uuid import uuid4

from celery import chain, chord, group
from sqlalchemy import select

from app.agents.graph.schemas import OrchestratorPlan
from app.db.session import SessionLocal
from app.models.task import Task
from app.schemas.enums import TaskStatus
from app.services.orchestrator_schedule_service import build_execution_waves
from app.workers.orchestrator_tasks import (
    finalize_orchestrator_execution,
    merge_orchestrator_wave,
    prepare_orchestrator_execution,
    prepare_orchestrator_wave,
    run_orchestrator_step,
)


def build_orchestrator_canvas(
    parent_task_id: int,
    children: list[Task],
    waves: list[dict],
):
    child_ids = {child.step_key: child.id for child in children}
    task_ids = []

    def tracked(signature):
        task_id = str(uuid4())
        task_ids.append(task_id)
        return signature.set(task_id=task_id)

    workflow = [tracked(prepare_orchestrator_execution.si(parent_task_id))]
    for wave in waves:
        wave_index = wave["index"]
        try:
            step_child_ids = [
                child_ids[step_key]
                for step_key in wave["step_ids"]
            ]
        except KeyError as exc:
            raise RuntimeError(
                f"Missing child task for orchestrator step: {exc.args[0]}"
            ) from exc
        workflow.extend(
            [
                tracked(prepare_orchestrator_wave.si(
                    parent_task_id,
                    wave_index,
                )),
                chord(
                    group(
                        tracked(run_orchestrator_step.si(child_id))
                        for child_id in step_child_ids
                    ),
                    tracked(merge_orchestrator_wave.si(
                        parent_task_id,
                        wave_index,
                    )),
                ),
            ]
        )
    workflow.append(tracked(finalize_orchestrator_execution.si(parent_task_id)))
    canvas = chain(*workflow)
    canvas._orchestrator_task_ids = task_ids
    return canvas


def _metadata(task: Task) -> dict:
    try:
        value = json.loads(task.metadata_json or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def dispatch_orchestrator_execution(
    parent_task_id: int,
    *,
    start_wave_index: int = 0,
) -> dict:
    with SessionLocal() as db:
        parent = db.scalar(
            select(Task)
            .where(Task.id == parent_task_id)
            .with_for_update()
        )
        if parent is None:
            return {"status": "failed", "error": "parent task not found"}
        metadata = _metadata(parent)
        if metadata.get("canvas_id"):
            return {
                "status": "dispatched",
                "canvas_id": metadata["canvas_id"],
            }
        try:
            plan = OrchestratorPlan.model_validate(
                {"steps": metadata.get("plan")}
            )
            waves = [
                {
                    "index": wave.index,
                    "step_ids": list(wave.step_ids),
                }
                for wave in build_execution_waves(plan.steps)
                if wave.index >= start_wave_index
            ]
            children = list(
                db.scalars(
                    select(Task)
                    .where(Task.parent_task_id == parent_task_id)
                    .order_by(Task.step_index.asc(), Task.id.asc())
                ).all()
            )
            canvas = build_orchestrator_canvas(
                parent_task_id,
                children,
                waves,
            )
            root_result = canvas.apply_async()
            task_ids = list(getattr(canvas, "_orchestrator_task_ids", ()))
            metadata["canvas_task_ids"] = task_ids or [root_result.id]
            metadata["canvas_id"] = metadata["canvas_task_ids"][0]
            metadata["plan_status"] = "dispatch_queued"
            parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
            db.commit()
            return {
                "status": "dispatched",
                "canvas_id": root_result.id,
            }
        except Exception as exc:
            parent.status = TaskStatus.FAILED
            parent.error_message = str(exc)
            metadata["plan_status"] = "dispatch_failed"
            parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
            db.commit()
            return {"status": "failed", "error": str(exc)}
