import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from git import Repo
from sqlalchemy import select

from app.agents.base import AgentRunRequest
from app.agents.graph.schemas import OrchestratorPlan
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.repository import Repository
from app.models.task import Task
from app.schemas.enums import MessageType, SenderType, TaskStatus
from app.services import repo_service, task_service
from app.services.orchestrator_schedule_service import build_execution_waves
from app.services.verification_service import verification_service
from app.services.worktree_service import WorktreeHandle, WorktreeService


@dataclass(frozen=True)
class StepExecutionOutcome:
    child_task_id: int
    status: str
    commit_hash: str | None
    changed_files: tuple[str, ...]
    verification_result: dict | None
    error: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _metadata(task: Task) -> dict:
    try:
        value = json.loads(task.metadata_json or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _repository(db, task: Task) -> Repository:
    conversation = db.get(Conversation, task.conversation_id)
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


def _children(db, parent_task_id: int) -> list[Task]:
    return list(
        db.scalars(
            select(Task)
            .where(Task.parent_task_id == parent_task_id)
            .order_by(Task.step_index.asc(), Task.id.asc())
        )
    )


def prepare_execution(parent_task_id: int) -> dict:
    with SessionLocal() as db:
        parent = db.get(Task, parent_task_id)
        if parent is None:
            return {"status": "failed", "error": "parent task not found"}
        metadata = _metadata(parent)
        if metadata.get("integration_worktree_path"):
            return {"status": "prepared", **metadata}
        try:
            plan = OrchestratorPlan.model_validate(
                {"steps": metadata.get("plan")}
            )
            waves = build_execution_waves(plan.steps)
            repository = _repository(db, parent)
            with _service(repository) as worktrees:
                base = worktrees.resolve_base_commit()
                integration = worktrees.ensure_integration_worktree(
                    parent_task_id,
                    base,
                )
            metadata.update(
                {
                    "plan_status": "dispatched",
                    "execution_waves": [
                        {
                            "index": wave.index,
                            "step_ids": list(wave.step_ids),
                        }
                        for wave in waves
                    ],
                    "integration_branch_name": integration.branch_name,
                    "integration_worktree_path": integration.path,
                    "base_commit_hash": base,
                    "result_commit_hash": None,
                    "canvas_id": metadata.get("canvas_id"),
                    "code_change_id": metadata.get("code_change_id"),
                }
            )
            by_key = {child.step_key: child for child in _children(db, parent.id)}
            for wave in waves:
                for step_id in wave.step_ids:
                    child = by_key[step_id]
                    child.wave_index = wave.index
                    child.base_commit_hash = base
                    child.merge_status = "pending"
            parent.status = TaskStatus.RUNNING
            parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
            db.commit()
            return {"status": "prepared", **metadata}
        except Exception as exc:
            parent.status = TaskStatus.FAILED
            parent.error_message = str(exc)
            metadata["plan_status"] = "prepare_failed"
            parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
            db.commit()
            return {"status": "failed", "error": str(exc)}


def prepare_wave(parent_task_id: int, wave_index: int) -> dict:
    with SessionLocal() as db:
        parent = db.get(Task, parent_task_id)
        if parent is None:
            return {"status": "failed", "error": "parent task not found"}
        if parent.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            return {"status": "skipped", "reason": "parent is terminal"}
        metadata = _metadata(parent)
        try:
            repository = _repository(db, parent)
            wave_children = [
                child
                for child in _children(db, parent.id)
                if child.wave_index == wave_index
            ]
            with _service(repository) as worktrees:
                integration_head = worktrees.resolve_base_commit(
                    metadata["integration_branch_name"]
                )
                for child in wave_children:
                    if child.status == TaskStatus.SUCCESS and child.result_commit_hash:
                        continue
                    handle = worktrees.ensure_step_worktree(
                        parent.id,
                        child.step_key,
                        integration_head,
                    )
                    child.worktree_path = handle.path
                    child.branch_name = handle.branch_name
                    child.base_commit_hash = integration_head
            db.commit()
            return {
                "status": "prepared",
                "wave_index": wave_index,
                "child_ids": [child.id for child in wave_children],
            }
        except Exception as exc:
            parent.status = TaskStatus.FAILED
            parent.error_message = str(exc)
            db.commit()
            return {"status": "failed", "error": str(exc)}


def _changed_files(path: str) -> list[str]:
    with Repo(path) as repo:
        lines = repo.git.status("--porcelain").splitlines()
    return [line[3:].replace("\\", "/") for line in lines if len(line) > 3]


def execute_step(
    child_task_id: int,
    celery_task_id: str,
) -> StepExecutionOutcome:
    with SessionLocal() as db:
        child = db.get(Task, child_task_id)
        if child is None:
            return StepExecutionOutcome(
                child_task_id, "FAILED", None, (), None, "child task not found"
            )
        parent = db.get(Task, child.parent_task_id)
        if child.status == TaskStatus.SUCCESS and child.result_commit_hash:
            return StepExecutionOutcome(
                child.id,
                "SUCCESS",
                child.result_commit_hash,
                tuple(json.loads(child.write_scope_json or "[]")),
                json.loads(child.verification_result_json or "null"),
                None,
            )
        if parent is None or parent.status in (
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            child.status = TaskStatus.CANCELLED
            db.commit()
            return StepExecutionOutcome(child.id, "CANCELLED", None, (), None, None)
        child.celery_task_id = celery_task_id
        child.status = TaskStatus.RUNNING
        child.started_at = _utcnow()
        db.commit()
        try:
            repository = _repository(db, parent)
            handle = WorktreeHandle(
                child.worktree_path,
                child.branch_name,
                child.base_commit_hash,
            )
            with _service(repository) as worktrees:
                worktrees.reset_step_worktree(handle, child.base_commit_hash)
                agent = task_service.get_or_create_agent(db, child.agent.code)
                adapter = task_service.get_adapter(agent)
                dependencies = {
                    item.step_key: item.result_summary
                    for item in _children(db, parent.id)
                    if item.step_key in json.loads(child.depends_on or "[]")
                }
                verification = None
                result = None
                for attempt in range(
                    settings.orchestrator_step_max_repair_attempts + 1
                ):
                    result = asyncio.run(
                        adapter.run(
                            AgentRunRequest(
                                task_id=child.id,
                                conversation_id=child.conversation_id,
                                instruction=child.instruction,
                                repo_path=handle.path,
                                repository_id=repository.id,
                                user_id=repository.user_id,
                                context={
                                    "dependency_results": dependencies,
                                    "verification_failure": (
                                        verification.failure_summary
                                        if verification is not None
                                        else None
                                    ),
                                },
                                task=child,
                            )
                        )
                    )
                    if result.status != "success":
                        raise RuntimeError(result.summary or "Agent execution failed")
                    changed = _changed_files(handle.path)
                    verification = verification_service.verify(
                        repository_id=repository.id,
                        user_id=repository.user_id,
                        changed_files=changed,
                        instruction=child.instruction,
                        workspace_path=handle.path,
                    )
                    if verification.success:
                        break
                    if attempt >= settings.orchestrator_step_max_repair_attempts:
                        raise RuntimeError(
                            verification.failure_summary or "Verification failed"
                        )
                if parent.status == TaskStatus.CANCELLED:
                    raise RuntimeError("Parent task was cancelled")
                committed = worktrees.commit_step_changes(
                    handle,
                    f"agent: execute {child.step_key}",
                )
            child.status = TaskStatus.SUCCESS
            child.result_commit_hash = committed.commit_hash
            child.merge_status = "ready" if committed.has_changes else "skipped"
            child.result_summary = result.summary
            child.verification_result_json = json.dumps(
                verification.model_dump(),
                ensure_ascii=False,
            )
            child.finished_at = _utcnow()
            db.commit()
            return StepExecutionOutcome(
                child.id,
                "SUCCESS",
                committed.commit_hash,
                committed.changed_files,
                verification.model_dump(),
                None,
            )
        except Exception as exc:
            child.status = TaskStatus.FAILED
            child.error_message = str(exc)
            child.finished_at = _utcnow()
            db.commit()
            return StepExecutionOutcome(
                child.id, "FAILED", None, (), None, str(exc)
            )


def merge_wave(parent_task_id: int, wave_index: int) -> dict:
    with SessionLocal() as db:
        parent = db.get(Task, parent_task_id)
        if parent is None:
            return {"status": "failed", "error": "parent task not found"}
        children = [
            child for child in _children(db, parent.id)
            if child.wave_index == wave_index
        ]
        if any(child.status == TaskStatus.FAILED for child in children):
            parent.status = TaskStatus.FAILED
            parent.error_message = f"Wave {wave_index} contains failed steps"
            db.commit()
            return {"status": "failed", "error": parent.error_message}
        metadata = _metadata(parent)
        try:
            repository = _repository(db, parent)
            integration = WorktreeHandle(
                metadata["integration_worktree_path"],
                metadata["integration_branch_name"],
                metadata["base_commit_hash"],
            )
            with _service(repository) as worktrees:
                for child in sorted(children, key=lambda item: item.step_index):
                    if child.merge_status == "merged":
                        continue
                    if child.result_commit_hash:
                        merged = worktrees.merge_step_commit(
                            integration,
                            child.result_commit_hash,
                        )
                        if not merged.success:
                            child.merge_status = "conflict"
                            child.error_message = json.dumps(
                                list(merged.conflict_files)
                            )
                            parent.status = TaskStatus.FAILED
                            parent.error_message = merged.error
                            db.commit()
                            return {
                                "status": "conflict",
                                "conflict_files": list(merged.conflict_files),
                            }
                    child.merge_status = (
                        "merged" if child.result_commit_hash else "skipped"
                    )
                    worktrees.remove_worktree(child.worktree_path)
                    worktrees.cleanup_step_branch(child.branch_name)
            db.commit()
            return {"status": "merged", "wave_index": wave_index}
        except Exception as exc:
            parent.status = TaskStatus.FAILED
            parent.error_message = str(exc)
            db.commit()
            return {"status": "failed", "error": str(exc)}


def finalize_execution(parent_task_id: int) -> dict:
    with SessionLocal() as db:
        parent = db.get(Task, parent_task_id)
        if parent is None:
            return {"status": "failed", "error": "parent task not found"}
        metadata = _metadata(parent)
        if metadata.get("code_change_id"):
            return {"status": "success", "code_change_id": metadata["code_change_id"]}
        if parent.status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            return {"status": str(parent.status), "error": parent.error_message}
        try:
            repository = _repository(db, parent)
            with _service(repository) as worktrees:
                result = worktrees.resolve_base_commit(
                    metadata["integration_branch_name"]
                )
                diff = worktrees.diff_between(
                    metadata["integration_worktree_path"],
                    metadata["base_commit_hash"],
                    result,
                )
            verification = verification_service.verify(
                repository_id=repository.id,
                user_id=repository.user_id,
                changed_files=list(diff.changed_files),
                instruction=parent.instruction,
                workspace_path=metadata["integration_worktree_path"],
            )
            if not verification.success:
                raise RuntimeError(
                    verification.failure_summary or "Final verification failed"
                )
            code_change = asyncio.run(
                repo_service.generate_code_change(
                    db,
                    parent,
                    repository,
                    workspace_path=metadata["integration_worktree_path"],
                    branch_name=metadata["integration_branch_name"],
                    base_commit_hash=metadata["base_commit_hash"],
                    result_commit_hash=result,
                )
            )
            metadata["result_commit_hash"] = result
            metadata["code_change_id"] = code_change.id
            metadata["plan_status"] = "executed"
            parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
            parent.status = TaskStatus.SUCCESS
            children = _children(db, parent.id)
            parent.result_summary = task_service.build_orchestrator_summary(
                parent,
                children,
            )
            parent.finished_at = _utcnow()
            summary_message = Message(
                conversation_id=parent.conversation_id,
                sender_type=SenderType.AGENT,
                sender_id=parent.agent_id,
                content=parent.result_summary,
                message_type=MessageType.TEXT,
            )
            db.add(summary_message)
            db.commit()
            db.refresh(parent)
            db.refresh(summary_message)
            asyncio.run(
                task_service.broadcast_task_event(parent, "task.updated")
            )
            asyncio.run(
                task_service.broadcast_agent_message(summary_message)
            )
            with _service(repository) as worktrees:
                for child in children:
                    if child.worktree_path:
                        worktrees.remove_worktree(child.worktree_path)
                    if child.branch_name:
                        worktrees.cleanup_step_branch(child.branch_name)
                worktrees.prune()
            return {"status": "success", "code_change_id": code_change.id}
        except Exception as exc:
            parent.status = TaskStatus.FAILED
            parent.error_message = str(exc)
            metadata["plan_status"] = "execution_failed"
            parent.metadata_json = json.dumps(metadata, ensure_ascii=False)
            db.commit()
            return {"status": "failed", "error": str(exc)}


def cleanup_worktrees(parent_task_id: int, *, force: bool = False) -> dict:
    with SessionLocal() as db:
        parent = db.get(Task, parent_task_id)
        if parent is None:
            return {"status": "failed", "error": "parent task not found"}
        if (
            parent.status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
            and not force
        ):
            return {
                "status": "preserved",
                "reason": "terminal diagnostics are retained",
            }
        try:
            repository = _repository(db, parent)
            children = _children(db, parent.id)
            with _service(repository) as worktrees:
                for child in children:
                    if child.worktree_path:
                        worktrees.remove_worktree(child.worktree_path)
                    if child.branch_name:
                        worktrees.cleanup_step_branch(child.branch_name)
                worktrees.prune()
            return {"status": "cleaned"}
        except Exception as exc:
            parent.error_message = str(exc)
            db.commit()
            return {"status": "failed", "error": str(exc)}
