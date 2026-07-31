import json
from types import SimpleNamespace

import pytest
from git import Repo
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.agent import Agent
from app.models.code_change import CodeChange
from app.models.conversation import Conversation
from app.models.repository import Repository
from app.models.task import Task
from app.models.user import Base, User
from app.schemas.enums import TaskStatus
from app.services import orchestrator_execution_service as execution_service
from app.services import orchestrator_recovery_service as recovery_service


@pytest.fixture
def sqlite_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    try:
        yield engine, factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _seed_orchestrator(
    factory,
    repository_path,
    *,
    generation,
    parent_status=TaskStatus.RUNNING,
    child_status=TaskStatus.PENDING,
    child_merge_status="pending",
    child_celery_task_id=None,
    child_result_commit_hash=None,
    child_worktree_path="step-worktree",
    child_branch_name="step-branch",
):
    with factory() as db:
        user = User(
            username="orchestrator-user",
            email="orchestrator@example.com",
            password_hash="hash",
        )
        agent = Agent(
            name="Backend",
            code="backend",
            adapter_type="mock",
        )
        db.add_all([user, agent])
        db.flush()
        repository = Repository(
            user_id=user.id,
            name="repository",
            repo_url="https://example.invalid/repository.git",
            local_path=str(repository_path),
            default_branch="main",
        )
        db.add(repository)
        db.flush()
        conversation = Conversation(
            user_id=user.id,
            repository_id=repository.id,
            title="Orchestrator",
            type="orchestrator",
        )
        db.add(conversation)
        db.flush()
        parent = Task(
            conversation_id=conversation.id,
            agent_id=agent.id,
            status=parent_status,
            instruction="Implement the plan",
            metadata_json=json.dumps(
                {
                    "execution_generation": generation,
                    "integration_worktree_path": str(repository_path),
                    "integration_branch_name": (
                        "agent/orchestrator/integration"
                    ),
                    "base_commit_hash": "base",
                }
            ),
        )
        db.add(parent)
        db.flush()
        child = Task(
            conversation_id=conversation.id,
            parent_task_id=parent.id,
            agent_id=agent.id,
            status=child_status,
            instruction="Implement the backend",
            step_key="backend",
            step_index=0,
            wave_index=0,
            depends_on="[]",
            write_scope_json="[]",
            worktree_path=child_worktree_path,
            branch_name=child_branch_name,
            base_commit_hash="base",
            result_commit_hash=child_result_commit_hash,
            merge_status=child_merge_status,
            celery_task_id=child_celery_task_id,
            metadata_json=json.dumps(
                {"execution_generation": generation}
            ),
        )
        db.add(child)
        db.commit()
        return parent.id, child.id, repository.id


def _assert_no_execution_side_effect(*_args, **_kwargs):
    raise AssertionError("stale or duplicate delivery reached side effects")


def test_execute_step_claim_refreshes_stale_identity_with_two_sessions(
    monkeypatch,
    sqlite_session_factory,
    tmp_path,
):
    _, factory = sqlite_session_factory
    parent_id, child_id, _ = _seed_orchestrator(
        factory,
        tmp_path,
        generation=1,
    )
    stale_session = factory()
    stale_session.get(Task, parent_id)
    stale_session.get(Task, child_id)
    with factory() as retry_session:
        parent = retry_session.get(Task, parent_id)
        child = retry_session.get(Task, child_id)
        parent.metadata_json = json.dumps({"execution_generation": 2})
        child.metadata_json = json.dumps({"execution_generation": 2})
        child.worktree_path = "retry-worktree"
        retry_session.commit()

    monkeypatch.setattr(
        execution_service,
        "SessionLocal",
        lambda: stale_session,
    )
    monkeypatch.setattr(
        execution_service,
        "_repository",
        _assert_no_execution_side_effect,
    )

    outcome = execution_service.execute_step(
        child_id,
        "old-generation-step",
        1,
    )

    assert outcome.status == "SKIPPED"
    with factory() as check:
        child = check.get(Task, child_id)
        assert child.status == TaskStatus.PENDING
        assert child.celery_task_id is None
        assert child.started_at is None


def test_duplicate_broker_delivery_refreshes_running_claim_with_two_sessions(
    monkeypatch,
    sqlite_session_factory,
    tmp_path,
):
    _, factory = sqlite_session_factory
    parent_id, child_id, _ = _seed_orchestrator(
        factory,
        tmp_path,
        generation=3,
    )
    duplicate_session = factory()
    duplicate_session.get(Task, parent_id)
    duplicate_session.get(Task, child_id)
    with factory() as first_delivery:
        child = first_delivery.get(Task, child_id)
        child.status = TaskStatus.RUNNING
        child.celery_task_id = "prepared-stable-step-id"
        first_delivery.commit()

    monkeypatch.setattr(
        execution_service,
        "SessionLocal",
        lambda: duplicate_session,
    )
    monkeypatch.setattr(
        execution_service,
        "_repository",
        _assert_no_execution_side_effect,
    )

    outcome = execution_service.execute_step(
        child_id,
        "prepared-stable-step-id",
        3,
    )

    assert outcome.status == "SKIPPED"
    with factory() as check:
        child = check.get(Task, child_id)
        assert child.status == TaskStatus.RUNNING
        assert child.celery_task_id == "prepared-stable-step-id"


@pytest.mark.parametrize("completed_merge_status", ["merged", "skipped"])
def test_duplicate_merge_refreshes_completed_child_before_cherry_pick(
    monkeypatch,
    sqlite_session_factory,
    tmp_path,
    completed_merge_status,
):
    _, factory = sqlite_session_factory
    parent_id, child_id, _ = _seed_orchestrator(
        factory,
        tmp_path,
        generation=4,
        child_status=TaskStatus.SUCCESS,
        child_merge_status="ready",
        child_result_commit_hash="result-commit",
    )
    duplicate_session = factory()
    duplicate_session.get(Task, parent_id)
    duplicate_session.get(Task, child_id)
    with factory() as first_delivery:
        child = first_delivery.get(Task, child_id)
        child.merge_status = completed_merge_status
        first_delivery.commit()

    monkeypatch.setattr(
        execution_service,
        "SessionLocal",
        lambda: duplicate_session,
    )
    monkeypatch.setattr(
        execution_service,
        "_repository",
        _assert_no_execution_side_effect,
    )

    result = execution_service.merge_wave(parent_id, 0, 4)

    assert result == {
        "status": "merged",
        "wave_index": 0,
        "idempotent": True,
    }
    with factory() as check:
        assert check.get(Task, parent_id).status == TaskStatus.RUNNING
        assert (
            check.get(Task, child_id).merge_status
            == completed_merge_status
        )


def test_cancel_cleanup_does_not_delete_immediate_retry_worktree(
    monkeypatch,
    sqlite_session_factory,
    tmp_path,
):
    _, factory = sqlite_session_factory
    parent_id, child_id, _ = _seed_orchestrator(
        factory,
        tmp_path,
        generation=5,
        parent_status=TaskStatus.CANCELLED,
        child_status=TaskStatus.CANCELLED,
        child_worktree_path="shared-retry-worktree",
        child_branch_name="shared-retry-branch",
    )
    cleanup_session = factory()
    cleanup_session.get(Task, parent_id)
    cleanup_session.get(Task, child_id)
    with factory() as retry_session:
        parent = retry_session.get(Task, parent_id)
        child = retry_session.get(Task, child_id)
        parent.status = TaskStatus.RUNNING
        parent.metadata_json = json.dumps({"execution_generation": 6})
        child.status = TaskStatus.PENDING
        child.metadata_json = json.dumps({"execution_generation": 6})
        retry_session.commit()

    removed = []

    class Worktrees:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def remove_worktree(self, path):
            removed.append(path)

        def cleanup_step_branch(self, branch):
            removed.append(branch)

        def prune(self):
            removed.append("prune")

    monkeypatch.setattr(
        recovery_service,
        "SessionLocal",
        lambda: cleanup_session,
    )
    monkeypatch.setattr(
        recovery_service,
        "_repository",
        lambda *_: object(),
    )
    monkeypatch.setattr(
        recovery_service,
        "_service",
        lambda *_: Worktrees(),
    )

    result = recovery_service._cleanup_safe_step_worktrees(
        parent_id,
        5,
    )

    assert result == {
        "status": "skipped",
        "reason": "cancellation generation changed",
    }
    assert removed == []


def _create_committed_repository(path):
    path.mkdir()
    repo = Repo.init(path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "AgentHub")
        config.set_value("user", "email", "agenthub@example.com")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    repo.index.add(["base.txt"])
    base = repo.index.commit("base").hexsha
    branch = "agent/orchestrator/integration"
    repo.git.checkout("-b", branch)
    (path / "result.txt").write_text("result\n", encoding="utf-8")
    repo.index.add(["result.txt"])
    result = repo.index.commit("result").hexsha
    repo.close()
    return base, result, branch


class _FinalizerWorktrees:
    def __init__(self, result_commit):
        self.result_commit = result_commit

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def resolve_base_commit(self, _revision):
        return self.result_commit

    def diff_between(self, *_args):
        return SimpleNamespace(changed_files=("result.txt",))

    def remove_worktree(self, _path):
        return None

    def cleanup_step_branch(self, _branch):
        return None

    def prune(self):
        return None


def _configure_finalizer(
    monkeypatch,
    repository_path,
    base,
    result,
    branch,
):
    def worktrees(_repository):
        return _FinalizerWorktrees(result)

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(execution_service, "_service", worktrees)
    monkeypatch.setattr(
        execution_service.verification_service,
        "verify",
        lambda **_kwargs: SimpleNamespace(
            success=True,
            failure_summary=None,
        ),
    )
    monkeypatch.setattr(
        execution_service.task_service,
        "build_orchestrator_summary",
        lambda *_args: "Orchestrator complete",
    )
    monkeypatch.setattr(
        execution_service.task_service,
        "broadcast_task_event",
        noop,
    )
    monkeypatch.setattr(
        execution_service.task_service,
        "broadcast_agent_message",
        noop,
    )
    monkeypatch.setattr(
        "app.services.task_service.broadcast_task_log",
        noop,
    )


def _set_parent_git_metadata(factory, parent_id, path, base, branch):
    with factory() as db:
        parent = db.get(Task, parent_id)
        parent.metadata_json = json.dumps(
            {
                "execution_generation": 7,
                "integration_worktree_path": str(path),
                "integration_branch_name": branch,
                "base_commit_hash": base,
            }
        )
        db.commit()


def test_finalizer_commits_code_change_parent_and_success_once(
    monkeypatch,
    sqlite_session_factory,
    tmp_path,
):
    engine, factory = sqlite_session_factory
    repository_path = tmp_path / "repository"
    base, result, branch = _create_committed_repository(repository_path)
    parent_id, _, _ = _seed_orchestrator(
        factory,
        repository_path,
        generation=7,
    )
    _set_parent_git_metadata(
        factory,
        parent_id,
        repository_path,
        base,
        branch,
    )
    _configure_finalizer(
        monkeypatch,
        repository_path,
        base,
        result,
        branch,
    )

    class CountingSession(Session):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.commit_calls = 0

        def commit(self):
            self.commit_calls += 1
            return super().commit()

    db = CountingSession(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(execution_service, "SessionLocal", lambda: db)

    outcome = execution_service.finalize_execution(parent_id, 7)

    assert outcome["status"] == "success"
    assert db.commit_calls == 1
    with factory() as check:
        parent = check.get(Task, parent_id)
        metadata = json.loads(parent.metadata_json)
        assert parent.status == TaskStatus.SUCCESS
        assert metadata["code_change_id"] == outcome["code_change_id"]
        assert check.scalar(
            select(func.count(CodeChange.id)).where(
                CodeChange.task_id == parent_id
            )
        ) == 1


def test_finalizer_generation_barrier_rolls_back_unlinked_code_change(
    monkeypatch,
    sqlite_session_factory,
    tmp_path,
):
    _, factory = sqlite_session_factory
    repository_path = tmp_path / "repository"
    base, result, branch = _create_committed_repository(repository_path)
    parent_id, _, _ = _seed_orchestrator(
        factory,
        repository_path,
        generation=7,
    )
    _set_parent_git_metadata(
        factory,
        parent_id,
        repository_path,
        base,
        branch,
    )
    _configure_finalizer(
        monkeypatch,
        repository_path,
        base,
        result,
        branch,
    )
    original_generate = execution_service.repo_service.generate_code_change

    async def generate_then_replace_generation(
        db,
        task,
        repository,
        **kwargs,
    ):
        code_change = await original_generate(
            db,
            task,
            repository,
            **kwargs,
        )
        metadata = json.loads(task.metadata_json)
        metadata["execution_generation"] = 8
        task.metadata_json = json.dumps(metadata)
        return code_change

    monkeypatch.setattr(
        execution_service.repo_service,
        "generate_code_change",
        generate_then_replace_generation,
    )
    monkeypatch.setattr(execution_service, "SessionLocal", factory)

    outcome = execution_service.finalize_execution(parent_id, 7)

    assert outcome == {"status": "skipped", "reason": "stale execution"}
    with factory() as check:
        parent = check.get(Task, parent_id)
        assert parent.status == TaskStatus.RUNNING
        assert json.loads(parent.metadata_json)["execution_generation"] == 7
        assert check.scalar(
            select(func.count(CodeChange.id)).where(
                CodeChange.task_id == parent_id
            )
        ) == 0
