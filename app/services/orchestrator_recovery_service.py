import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.code_change import CodeChange
from app.models.conversation import Conversation
from app.models.repository import Repository
from app.models.task import Task
from app.schemas.enums import CodeChangeStatus, TaskStatus
from app.services import task_service
from app.services.worktree_service import WorktreeService
from app.workers.celery_app import celery_app


def _metadata(task: Task) -> dict:
    try:
        value = json.loads(getattr(task, "metadata_json", None) or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _children(db, parent_task_id: int) -> list[Task]:
    return list(
        db.scalars(
            select(Task)
            .where(Task.parent_task_id == parent_task_id)
            .order_by(Task.step_index.asc(), Task.id.asc())
        )
    )


def _repository(db, parent: Task) -> Repository:
    conversation = db.get(Conversation, parent.conversation_id)
    if conversation is None or conversation.repository_id is None:
        raise RuntimeError("Orchestrator task requires a repository")
    repository = db.get(Repository, conversation.repository_id)
    if repository is None:
        raise RuntimeError("Orchestrator repository was not found")
    return repository


def _service(repository: Repository) -> WorktreeService:
    return WorktreeService(
        repository_id=repository.id,
        user_id=repository.user_id,
        repository_path=repository.local_path,
    )


def _broadcast_recovery_logs(parent: Task, actions: list[str]) -> None:
    for action in actions:
        asyncio.run(task_service.broadcast_task_log(parent, action))


def _persist_recovery_failure(
    parent_task_id: int,
    error: str,
    *,
    plan_status: str,
    status: TaskStatus | None = None,
    expected_generation: int | None = None,
) -> None:
    with SessionLocal() as db:
        parent = db.scalar(
            select(Task)
            .where(Task.id == parent_task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if parent is None:
            return
        metadata = _metadata(parent)
        generation = int(metadata.get("execution_generation", 0))
        if (
            expected_generation is not None
            and (
                generation != expected_generation
                or (
                    status is not None
                    and parent.status
                    in (TaskStatus.CANCELLED, TaskStatus.CANCELLED.value)
                )
            )
        ):
            recovery_errors = metadata.get("recovery_errors")
            if not isinstance(recovery_errors, list):
                recovery_errors = []
            recovery_errors.append(
                {
                    "plan_status": plan_status,
                    "execution_generation": expected_generation,
                    "error": error,
                }
            )
            metadata["recovery_errors"] = recovery_errors
            parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
            db.commit()
            return
        metadata["plan_status"] = plan_status
        parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
        parent.error_message = error
        if status is not None:
            parent.status = status
        db.commit()


def _cleanup_safe_step_worktrees(parent_task_id: int) -> None:
    with SessionLocal() as db:
        parent = db.get(Task, parent_task_id)
        if parent is None:
            return
        repository = _repository(db, parent)
        with _service(repository) as worktrees:
            for child in _children(db, parent.id):
                safe = (
                    child.celery_task_id is None
                    or child.merge_status == "merged"
                )
                if not safe:
                    continue
                if child.worktree_path:
                    worktrees.remove_worktree(child.worktree_path)
                if child.branch_name:
                    worktrees.cleanup_step_branch(child.branch_name)
            worktrees.prune()


def cancel_orchestrator(parent_task_id: int) -> dict:
    revoke_ids: set[str] = set()
    cancelled_generation: int | None = None
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
        for task_id in (
            metadata.get("canvas_id"),
            parent.celery_task_id,
            *(metadata.get("canvas_task_ids") or []),
        ):
            if task_id:
                revoke_ids.add(str(task_id))
        if parent.status not in (
            TaskStatus.PENDING,
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING,
            TaskStatus.RUNNING.value,
        ):
            return {"status": "conflict", "error": "task is terminal"}
        cancelled_generation = (
            int(metadata.get("execution_generation", 0)) + 1
        )
        metadata["execution_generation"] = cancelled_generation
        parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
        now = datetime.now(UTC)
        parent.status = TaskStatus.CANCELLED
        parent.finished_at = now
        children = list(
            db.scalars(
                select(Task)
                .where(Task.parent_task_id == parent.id)
                .order_by(Task.step_index.asc(), Task.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        for child in children:
            if child.celery_task_id:
                revoke_ids.add(str(child.celery_task_id))
            if child.status in (
                TaskStatus.PENDING,
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING,
                TaskStatus.RUNNING.value,
            ):
                child.status = TaskStatus.CANCELLED
                child.finished_at = now
        db.commit()

    cleanup_error = None
    failed_revocations = {}
    succeeded_revocations = []
    for task_id in sorted(revoke_ids):
        try:
            celery_app.control.revoke(task_id, terminate=True)
            succeeded_revocations.append(task_id)
        except Exception as exc:
            failed_revocations[task_id] = str(exc)
    try:
        _cleanup_safe_step_worktrees(parent_task_id)
    except Exception as exc:
        cleanup_error = str(exc)
    if cleanup_error or failed_revocations:
        errors = {
            "cleanup_error": cleanup_error,
            "failed_revocations": failed_revocations,
        }
        _persist_recovery_failure(
            parent_task_id,
            json.dumps(errors, ensure_ascii=False),
            plan_status="cancellation_cleanup_failed",
            expected_generation=cancelled_generation,
        )
    result = {
        "status": "cancelled",
        "revoked_task_ids": succeeded_revocations,
    }
    if failed_revocations:
        result["failed_revocations"] = failed_revocations
    if cleanup_error:
        result["cleanup_error"] = cleanup_error
    return result


def retry_failed_orchestrator(parent_task_id: int) -> str:
    from app.services import orchestrator_dispatch_service

    generation = 0
    with SessionLocal() as db:
        parent = db.scalar(
            select(Task)
            .where(Task.id == parent_task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if parent is None:
            raise RuntimeError("parent task not found")
        if parent.status not in (
            TaskStatus.FAILED,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED,
            TaskStatus.CANCELLED.value,
        ):
            raise ValueError(
                "Only FAILED or CANCELLED orchestrators can be retried"
            )
        children = list(
            db.scalars(
                select(Task)
                .where(Task.parent_task_id == parent.id)
                .order_by(Task.step_index.asc(), Task.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        generation = int(_metadata(parent).get("execution_generation", 0)) + 1
        incomplete = [
            child
            for child in children
            if not (
                child.status in (
                    TaskStatus.SUCCESS,
                    TaskStatus.SUCCESS.value,
                )
                and child.merge_status in ("merged", "skipped")
            )
        ]
        first_wave = min(
            (
                child.wave_index
                for child in incomplete
                if child.wave_index is not None
            ),
            default=0,
        )
        for child in incomplete:
            child.status = TaskStatus.PENDING
            child.celery_task_id = None
            child.result_commit_hash = None
            child.merge_status = "pending"
            child.verification_result_json = None
            child.result_summary = None
            child.error_message = None
            child.started_at = None
            child.finished_at = None
            child_metadata = _metadata(child)
            child_metadata["execution_generation"] = generation
            child.metadata_json = json.dumps(child_metadata, ensure_ascii=False)
        metadata = _metadata(parent)
        metadata["execution_generation"] = generation
        metadata["canvas_id"] = None
        metadata["execution_id"] = None
        metadata["plan_status"] = "retrying"
        metadata["retry_start_wave"] = first_wave
        parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
        parent.status = TaskStatus.RUNNING
        parent.error_message = None
        parent.finished_at = None
        db.commit()

    result = orchestrator_dispatch_service.dispatch_orchestrator_execution(
        parent_task_id,
        start_wave_index=first_wave,
    )
    if result.get("status") != "dispatched":
        error = result.get("error") or "retry dispatch failed"
        _persist_recovery_failure(
            parent_task_id,
            error,
            plan_status="retry_dispatch_failed",
            status=TaskStatus.FAILED,
            expected_generation=generation,
        )
        raise RuntimeError(error)
    return str(result["canvas_id"])


def reconcile_orchestrator(parent_task_id: int) -> dict:
    actions: list[str] = []
    with SessionLocal() as db:
        parent = db.get(Task, parent_task_id)
        if parent is None:
            return {"status": "failed", "error": "parent task not found"}
        metadata = _metadata(parent)
        try:
            repository = _repository(db, parent)
            children = _children(db, parent.id)
            with _service(repository) as worktrees:
                integration_path = metadata.get(
                    "integration_worktree_path"
                )
                integration_branch = metadata.get(
                    "integration_branch_name"
                )
                if (
                    integration_path
                    and metadata.get("base_commit_hash")
                    and not worktrees.worktree_exists(integration_path)
                ):
                    integration = worktrees.ensure_integration_worktree(
                        parent.id,
                        metadata["base_commit_hash"],
                    )
                    metadata["integration_worktree_path"] = integration.path
                    metadata["integration_branch_name"] = integration.branch_name
                    integration_path = integration.path
                    integration_branch = integration.branch_name
                    parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
                    actions.append("recreated missing integration worktree")
                if (
                    integration_path
                    and worktrees.abort_cherry_pick(integration_path)
                ):
                    actions.append(
                        "aborted residual integration cherry-pick"
                    )
                for child in children:
                    if child.worktree_path and worktrees.abort_cherry_pick(
                        child.worktree_path
                    ):
                        actions.append(
                            f"aborted residual cherry-pick for {child.step_key}"
                        )
                    if (
                        child.merge_status != "merged"
                        and child.base_commit_hash
                        and (
                            not child.worktree_path
                            or not worktrees.worktree_exists(
                                child.worktree_path
                            )
                        )
                    ):
                        handle = worktrees.ensure_step_worktree(
                            parent.id,
                            child.step_key,
                            child.base_commit_hash,
                        )
                        child.worktree_path = handle.path
                        child.branch_name = handle.branch_name
                        actions.append(
                            f"recreated missing worktree for {child.step_key}"
                        )
                    if (
                        child.result_commit_hash
                        and integration_branch
                        and worktrees.commit_is_ancestor(
                            child.result_commit_hash,
                            integration_branch,
                        )
                    ):
                        if child.merge_status != "merged":
                            child.merge_status = "merged"
                            actions.append(
                                f"reconciled merged commit for {child.step_key}"
                            )
                    elif child.merge_status == "merged":
                        child.merge_status = "ready"
                        actions.append(
                            f"reopened missing merge for {child.step_key}"
                        )
                worktrees.prune()
            db.commit()
            _broadcast_recovery_logs(parent, actions)
            return {"status": "reconciled", "actions": actions}
        except Exception as exc:
            parent.error_message = str(exc)
            db.commit()
            return {"status": "failed", "error": str(exc), "actions": actions}


def cleanup_terminal_orchestrator(
    parent_task_id: int,
    force: bool = False,
) -> dict:
    with SessionLocal() as db:
        parent = db.get(Task, parent_task_id)
        if parent is None:
            return {"status": "failed", "error": "parent task not found"}
        if parent.status not in (
            TaskStatus.SUCCESS,
            TaskStatus.SUCCESS.value,
            TaskStatus.FAILED,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED,
            TaskStatus.CANCELLED.value,
        ):
            return {"status": "skipped", "reason": "task is not terminal"}
        if parent.status in (TaskStatus.FAILED, TaskStatus.CANCELLED) and not force:
            return {
                "status": "preserved",
                "reason": "terminal diagnostics are retained",
            }
        metadata = _metadata(parent)
        code_change = (
            db.get(CodeChange, metadata["code_change_id"])
            if metadata.get("code_change_id")
            else None
        )
        protected_statuses = {
            CodeChangeStatus.GENERATED,
            CodeChangeStatus.GENERATED.value,
            CodeChangeStatus.ACCEPTED,
            CodeChangeStatus.ACCEPTED.value,
            CodeChangeStatus.COMMITTED,
            CodeChangeStatus.COMMITTED.value,
        }
        integration_preserved = (
            code_change is not None
            and code_change.status in protected_statuses
        )
        try:
            repository = _repository(db, parent)
            with _service(repository) as worktrees:
                for child in _children(db, parent.id):
                    if child.worktree_path:
                        worktrees.remove_worktree(child.worktree_path)
                    if child.branch_name:
                        worktrees.cleanup_step_branch(child.branch_name)
                if (
                    force
                    and not integration_preserved
                    and metadata.get("integration_worktree_path")
                    and metadata.get("integration_branch_name")
                ):
                    worktrees.cleanup_integration_branch(
                        metadata["integration_worktree_path"],
                        metadata["integration_branch_name"],
                    )
                worktrees.prune()
            return {
                "status": "cleaned",
                "integration_preserved": integration_preserved,
            }
        except Exception as exc:
            parent.error_message = str(exc)
            db.commit()
            return {"status": "failed", "error": str(exc)}
