import json

from sqlalchemy import select

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.task import Task
from app.models.user import User
from app.schemas.enums import TaskStatus
from app.services import task_service
from app.workers import agent_tasks


class DummyAsyncResult:
    id = "resume-celery-task-id"


def create_user_and_headers(client, db_session, suffix):
    username = f"resumeuser{suffix}"
    client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "password123"},
    )
    user = db_session.scalar(select(User).where(User.username == username))
    return user, {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_awaiting_task(db_session, user, suffix):
    conversation = Conversation(user_id=user.id, title="Resume plan")
    agent = Agent(
        name="Resume Orchestrator",
        code=f"resume-orchestrator-{suffix}",
        adapter_type="langgraph",
    )
    db_session.add_all([conversation, agent])
    db_session.commit()

    task = Task(
        conversation_id=conversation.id,
        agent_id=agent.id,
        status=TaskStatus.PENDING,
        instruction="resume this plan",
        metadata_json=json.dumps(
            {
                "requires_plan_confirmation": True,
                "plan_status": "awaiting_confirmation",
                "plan": [
                    {"agent": "backend", "instruction": "implement resume"}
                ],
            }
        ),
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)
    return task


def install_dispatch_recorders(monkeypatch):
    resume_calls = []
    run_calls = []

    monkeypatch.setattr(
        agent_tasks.resume_orchestrator_task,
        "delay",
        lambda task_id, payload: (
            resume_calls.append((task_id, payload)) or DummyAsyncResult()
        ),
    )
    monkeypatch.setattr(
        agent_tasks.run_orchestrator_task,
        "delay",
        lambda task_id: run_calls.append(task_id) or DummyAsyncResult(),
    )
    return resume_calls, run_calls


def test_confirm_dispatches_resume_with_approval_payload(
    client,
    db_session,
    monkeypatch,
):
    user, headers = create_user_and_headers(client, db_session, "dispatch")
    task = create_awaiting_task(db_session, user, "dispatch")
    resume_calls, run_calls = install_dispatch_recorders(monkeypatch)
    broadcasts = []

    async def record_broadcast(task_arg, event_name):
        broadcasts.append(
            (task_arg.id, task_arg.celery_task_id, event_name)
        )

    monkeypatch.setattr(
        task_service,
        "broadcast_task_event",
        record_broadcast,
    )

    response = client.post(
        f"/api/tasks/{task.id}/plan/confirm",
        headers=headers,
    )

    assert response.status_code == 200
    assert resume_calls == [(task.id, {"approved": True})]
    assert run_calls == []
    assert response.json()["celery_task_id"] == "resume-celery-task-id"
    assert broadcasts == [
        (task.id, "resume-celery-task-id", "task.updated")
    ]


def test_confirm_twice_dispatches_resume_only_once(
    client,
    db_session,
    monkeypatch,
):
    user, headers = create_user_and_headers(client, db_session, "duplicate")
    task = create_awaiting_task(db_session, user, "duplicate")
    resume_calls, run_calls = install_dispatch_recorders(monkeypatch)

    first_response = client.post(
        f"/api/tasks/{task.id}/plan/confirm",
        headers=headers,
    )
    second_response = client.post(
        f"/api/tasks/{task.id}/plan/confirm",
        headers=headers,
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 400
    assert "awaiting confirmation" in second_response.json()["detail"]
    assert resume_calls == [(task.id, {"approved": True})]
    assert run_calls == []
