from datetime import datetime

from app.agents.base import AgentRunResult
from app.models.agent import Agent
from app.models.task import Task
from app.schemas.enums import TaskStatus
from app.workers import agent_tasks


class FakeSession:
    def __init__(self, task, agent=None):
        self.task = task
        self.agent = agent
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def get(self, model, object_id):
        if model is Task and object_id == self.task.id:
            return self.task
        if model is Agent and self.agent and object_id == self.agent.id:
            return self.agent
        return None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, instance):
        return None

    def close(self):
        self.closed = True


def make_orchestrator_task(status=TaskStatus.PENDING, finished_at=None):
    return Task(
        id=101,
        conversation_id=202,
        agent_id=303,
        status=status,
        instruction="Implement durable approval",
        finished_at=finished_at,
    )


def install_broadcast_recorder(monkeypatch):
    events = []

    async def fake_broadcast_task_event(task, event_name):
        events.append((task.status, task.finished_at, event_name))

    monkeypatch.setattr(
        agent_tasks.task_service,
        "broadcast_task_event",
        fake_broadcast_task_event,
    )
    return events


def test_initial_orchestrator_interrupt_returns_task_to_pending(monkeypatch):
    task = make_orchestrator_task()
    agent = Agent(
        id=task.agent_id,
        name="Orchestrator",
        code="orchestrator",
        adapter_type="langgraph",
    )
    session = FakeSession(task, agent)

    class InterruptingAdapter:
        async def run(self, request):
            return AgentRunResult(
                status="awaiting_confirmation",
                summary=(
                    "Orchestrator plan generated and is awaiting confirmation."
                ),
                changed_files=[],
                logs="LangGraph interrupted for human approval.",
            )

    events = install_broadcast_recorder(monkeypatch)
    monkeypatch.setattr(agent_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        agent_tasks.task_service,
        "get_adapter",
        lambda agent_arg: InterruptingAdapter(),
    )

    result = agent_tasks.run_orchestrator_task.run(task.id)

    assert task.status == TaskStatus.PENDING
    assert task.finished_at is None
    assert session.commits == 2
    assert session.closed is True
    assert events == [
        (TaskStatus.RUNNING, None, "task.updated"),
        (TaskStatus.PENDING, None, "task.updated"),
    ]
    assert "awaiting confirmation" in result


def test_initial_orchestrator_success_is_left_to_summarizer(monkeypatch):
    task = make_orchestrator_task()
    agent = Agent(
        id=task.agent_id,
        name="Orchestrator",
        code="orchestrator-success",
        adapter_type="langgraph",
    )
    session = FakeSession(task, agent)
    summarized_at = datetime(2026, 7, 21, 12, 0, 0)

    class CompletingAdapter:
        async def run(self, request):
            task.status = TaskStatus.SUCCESS
            task.finished_at = summarized_at
            return AgentRunResult(status="success", summary="done")

    events = install_broadcast_recorder(monkeypatch)
    monkeypatch.setattr(agent_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        agent_tasks.task_service,
        "get_adapter",
        lambda agent_arg: CompletingAdapter(),
    )

    result = agent_tasks.run_orchestrator_task.run(task.id)

    assert task.status == TaskStatus.SUCCESS
    assert task.finished_at == summarized_at
    assert session.commits == 1
    assert session.closed is True
    assert events == [(TaskStatus.RUNNING, None, "task.updated")]
    assert result == "LangGraph Orchestrator completed: done"


def test_resume_orchestrator_uses_command_resume_path(monkeypatch):
    old_finished_at = datetime(2026, 7, 20, 12, 0, 0)
    task = make_orchestrator_task(finished_at=old_finished_at)
    session = FakeSession(task)
    summarized_at = datetime(2026, 7, 21, 13, 0, 0)
    resume_calls = []

    class ResumingAdapter:
        async def resume(self, task_id, resume_value):
            resume_calls.append((task_id, resume_value))
            task.status = TaskStatus.SUCCESS
            task.finished_at = summarized_at
            return AgentRunResult(status="success", summary="resumed")

    events = install_broadcast_recorder(monkeypatch)
    monkeypatch.setattr(agent_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        agent_tasks,
        "LangGraphOrchestratorAdapter",
        ResumingAdapter,
        raising=False,
    )

    result = agent_tasks.resume_orchestrator_task.run(
        task.id,
        {"approved": True},
    )

    assert resume_calls == [(task.id, {"approved": True})]
    assert task.status == TaskStatus.SUCCESS
    assert task.finished_at == summarized_at
    assert session.commits == 1
    assert session.closed is True
    assert events == [(TaskStatus.RUNNING, None, "task.updated")]
    assert result == "LangGraph Orchestrator resumed: resumed"


def test_resume_orchestrator_failure_marks_task_failed(monkeypatch):
    task = make_orchestrator_task()
    session = FakeSession(task)

    class FailingAdapter:
        async def resume(self, task_id, resume_value):
            raise RuntimeError("resume failed")

    events = install_broadcast_recorder(monkeypatch)
    monkeypatch.setattr(agent_tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        agent_tasks,
        "LangGraphOrchestratorAdapter",
        FailingAdapter,
        raising=False,
    )

    result = agent_tasks.resume_orchestrator_task.run(
        task.id,
        {"approved": True},
    )

    assert task.status == TaskStatus.FAILED
    assert task.error_message == "RuntimeError: resume failed"
    assert task.finished_at is not None
    assert session.commits == 2
    assert session.rollbacks == 1
    assert session.closed is True
    assert events[0] == (TaskStatus.RUNNING, None, "task.updated")
    assert events[1][0] == TaskStatus.FAILED
    assert events[1][2] == "task.updated"
    assert result == "Orchestrator resume failed: RuntimeError: resume failed"
