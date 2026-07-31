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
    execution_generation: int = 0,
    *,
    task_ids: list[str] | None = None,
):
    child_ids = {child.step_key: child.id for child in children}
    assigned_task_ids = []
    prepared_task_ids = iter(task_ids) if task_ids is not None else None

    def tracked(signature):
        if prepared_task_ids is None:
            task_id = str(uuid4())
        else:
            try:
                task_id = str(next(prepared_task_ids))
            except StopIteration as exc:
                raise RuntimeError(
                    "Prepared orchestrator task IDs are incomplete"
                ) from exc
        assigned_task_ids.append(task_id)
        return signature.set(task_id=task_id)

    prepare_args = (parent_task_id, execution_generation) if execution_generation else (parent_task_id,)
    workflow = [tracked(prepare_orchestrator_execution.si(*prepare_args))]
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
                tracked(prepare_orchestrator_wave.si(parent_task_id, wave_index, *([execution_generation] if execution_generation else []))),
                chord(
                    group(
                        tracked(run_orchestrator_step.si(child_id, *([execution_generation] if execution_generation else [])))
                        for child_id in step_child_ids
                    ),
                    tracked(merge_orchestrator_wave.si(parent_task_id, wave_index, *([execution_generation] if execution_generation else []))),
                ),
            ]
        )
    workflow.append(tracked(finalize_orchestrator_execution.si(parent_task_id, *([execution_generation] if execution_generation else []))))
    canvas = chain(*workflow)
    if prepared_task_ids is not None:
        try:
            next(prepared_task_ids)
        except StopIteration:
            pass
        else:
            raise RuntimeError(
                "Prepared orchestrator task IDs do not match the Canvas"
            )
    canvas._orchestrator_task_ids = assigned_task_ids
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
            .execution_options(populate_existing=True)
        )
        if parent is None:
            return {"status": "failed", "error": "parent task not found"}
        metadata = _metadata(parent)
        if (
            metadata.get("plan_status") == "dispatch_queued"
            and metadata.get("canvas_id")
        ):
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
            generation = int(metadata.get("execution_generation", 0)) or 1
            execution_id = metadata.get("execution_id") or str(uuid4())
            canvas_id = metadata.get("canvas_id") or execution_id
            prepared_task_ids = (
                list(metadata["canvas_task_ids"])
                if (
                    metadata.get("plan_status") == "dispatch_prepared"
                    and isinstance(metadata.get("canvas_task_ids"), list)
                    and metadata["canvas_task_ids"]
                )
                else None
            )
            canvas = build_orchestrator_canvas(
                parent_task_id,
                children,
                waves,
                generation,
                task_ids=prepared_task_ids,
            )
            task_ids = list(getattr(canvas, "_orchestrator_task_ids", ()))
            metadata["execution_generation"] = generation
            metadata["execution_id"] = execution_id
            metadata["canvas_task_ids"] = task_ids
            metadata["canvas_id"] = canvas_id
            metadata["plan_status"] = "dispatch_prepared"
            parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
            db.commit()
        except Exception as exc:
            parent.status = TaskStatus.FAILED
            parent.error_message = str(exc)
            metadata["plan_status"] = "dispatch_failed"
            parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
            db.commit()
            return {"status": "failed", "error": str(exc)}

        try:
            canvas.apply_async()
            parent = db.scalar(
                select(Task)
                .where(Task.id == parent_task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if parent is None:
                return {
                    "status": "failed",
                    "error": "parent task not found after publish",
                }
            latest_metadata = _metadata(parent)
            if (
                int(latest_metadata.get("execution_generation", 0))
                != generation
                or parent.status
                in (
                    TaskStatus.SUCCESS,
                    TaskStatus.SUCCESS.value,
                    TaskStatus.FAILED,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED,
                    TaskStatus.CANCELLED.value,
                )
            ):
                return {
                    "status": "skipped",
                    "reason": "stale execution",
                    "canvas_id": canvas_id,
                }
            latest_metadata["plan_status"] = "dispatch_queued"
            parent.metadata_json = json.dumps(
                latest_metadata,
                ensure_ascii=False,
            )
            db.commit()
            return {
                "status": "dispatched",
                "canvas_id": canvas_id,
            }
        except Exception as exc:
            parent = db.scalar(
                select(Task)
                .where(Task.id == parent_task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if parent is None:
                return {"status": "failed", "error": str(exc)}
            latest_metadata = _metadata(parent)
            dispatch_errors = latest_metadata.get("dispatch_errors")
            if not isinstance(dispatch_errors, list):
                dispatch_errors = []
            dispatch_errors.append(
                {
                    "stage": "publish",
                    "execution_generation": generation,
                    "error": str(exc),
                }
            )
            latest_metadata["dispatch_errors"] = dispatch_errors
            current_generation = (
                int(latest_metadata.get("execution_generation", 0))
                == generation
            )
            cancelled = parent.status in (
                TaskStatus.CANCELLED,
                TaskStatus.CANCELLED.value,
            )
            if current_generation and not cancelled:
                parent.status = TaskStatus.FAILED
                parent.error_message = str(exc)
                latest_metadata["plan_status"] = "dispatch_failed"
            parent.metadata_json = json.dumps(
                latest_metadata,
                ensure_ascii=False,
            )
            db.commit()
            return {"status": "failed", "error": str(exc)}
