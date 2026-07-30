import json
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.task import Task
from app.models.user import User
from app.schemas.enums import TaskStatus


ORCHESTRATOR_COLUMNS = {
    "step_key",
    "step_index",
    "wave_index",
    "write_scope_json",
    "worktree_path",
    "branch_name",
    "base_commit_hash",
    "result_commit_hash",
    "merge_status",
    "verification_result_json",
}


def create_task_dependencies(db_session):
    suffix = uuid4().hex
    user = User(
        username=f"task-model-{suffix}",
        email=f"task-model-{suffix}@example.com",
        password_hash="test",
    )
    agent = Agent(
        name=f"Task Model {suffix}",
        code=f"task-model-{suffix}",
    )
    db_session.add_all([user, agent])
    db_session.flush()
    conversation = Conversation(
        user_id=user.id,
        title="Orchestrator task model",
    )
    db_session.add(conversation)
    db_session.flush()
    return conversation, agent


def test_task_model_declares_orchestrator_execution_columns_and_indexes():
    assert ORCHESTRATOR_COLUMNS <= set(Task.__table__.columns.keys())

    indexes = {
        index.name: (
            tuple(column.name for column in index.columns),
            index.unique,
        )
        for index in Task.__table__.indexes
    }
    assert indexes["ix_tasks_parent_task_id"] == (
        ("parent_task_id",),
        False,
    )
    assert indexes["ix_tasks_parent_step_key"] == (
        ("parent_task_id", "step_key"),
        True,
    )
    assert indexes["ix_tasks_parent_wave_index"] == (
        ("parent_task_id", "wave_index"),
        False,
    )


def test_orchestrator_step_fields_and_json_round_trip(db_session):
    conversation, agent = create_task_dependencies(db_session)
    parent = Task(
        conversation_id=conversation.id,
        agent_id=agent.id,
        status=TaskStatus.RUNNING,
        instruction="Run the orchestrator.",
        task_type="orchestrator",
    )
    db_session.add(parent)
    db_session.flush()

    child = Task(
        conversation_id=conversation.id,
        parent_task_id=parent.id,
        agent_id=agent.id,
        status=TaskStatus.PENDING,
        instruction="Implement the backend.",
        task_type="graph_subtask",
        step_key="backend-api",
        step_index=0,
        wave_index=0,
        depends_on=json.dumps(["schema"]),
        write_scope_json=json.dumps(["app/api", "tests/api"]),
        worktree_path="task_worktrees/user-1/repo-2/steps/backend-api",
        branch_name="agent/orchestrator-10/backend-api",
        base_commit_hash="a" * 40,
        result_commit_hash="b" * 40,
        merge_status="merged",
        verification_result_json=json.dumps({"success": True}),
    )
    db_session.add(child)
    db_session.flush()
    child_id = child.id
    db_session.expire_all()
    child = db_session.get(Task, child_id)

    assert json.loads(child.depends_on) == ["schema"]
    assert json.loads(child.write_scope_json) == ["app/api", "tests/api"]
    assert json.loads(child.verification_result_json) == {"success": True}
    assert child.step_key == "backend-api"
    assert child.step_index == 0
    assert child.wave_index == 0


def test_parent_step_key_unique_constraint(db_session):
    conversation, agent = create_task_dependencies(db_session)
    parent = Task(
        conversation_id=conversation.id,
        agent_id=agent.id,
        status=TaskStatus.RUNNING,
        instruction="Run the orchestrator.",
    )
    db_session.add(parent)
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            for instruction in ("First delivery.", "Duplicate delivery."):
                db_session.add(
                    Task(
                        conversation_id=conversation.id,
                        parent_task_id=parent.id,
                        agent_id=agent.id,
                        status=TaskStatus.PENDING,
                        instruction=instruction,
                        step_key="same-step",
                    )
                )
            db_session.flush()


def test_create_subtask_persists_empty_dependencies_as_json(
    db_session,
    monkeypatch,
):
    from app.services import task_service

    conversation, agent = create_task_dependencies(db_session)
    parent = Task(
        conversation_id=conversation.id,
        agent_id=agent.id,
        status=TaskStatus.RUNNING,
        instruction="Run the orchestrator.",
    )
    db_session.add(parent)
    db_session.flush()
    monkeypatch.setattr(
        task_service,
        "get_or_create_agent",
        lambda db, agent_code: agent,
    )
    monkeypatch.setattr(db_session, "commit", db_session.flush)

    child = task_service.create_subtask(
        db_session,
        parent,
        "backend",
        "Implement the backend.",
        task_type="graph_subtask",
        depends_on=[],
        step_key="backend",
        step_index=0,
        write_scope=["app"],
    )

    assert json.loads(child.depends_on) == []
    assert child.step_key == "backend"
    assert child.step_index == 0
    assert json.loads(child.write_scope_json) == ["app"]
