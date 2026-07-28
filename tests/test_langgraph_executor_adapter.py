import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.agents.base import AgentRunResult
from app.agents.graph import nodes
from app.schemas.enums import TaskStatus


class FakeExecutorDb:
    def __init__(self, child_task):
        self.child_task = child_task
        self.commits = 0
        self.closed = False

    def get(self, model, task_id):
        assert task_id == self.child_task.id
        return self.child_task

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def _executor_state(child_task_id: int, errors=None):
    return {
        "task_id": 100,
        "conversation_id": 7,
        "repo_path": "/trusted/workspace",
        "plan": [
            {
                "agent": "backend",
                "instruction": "Implement the API endpoint.",
            }
        ],
        "current_step_index": 0,
        "current_agent": "backend",
        "current_instruction": "Implement the API endpoint.",
        "metadata_json": json.dumps({"child_ids": [child_task_id]}),
        "errors": list(errors or []),
        "is_finished": False,
    }


def _install_executor_fakes(monkeypatch, adapter_result):
    from app.services import task_service

    child_task = SimpleNamespace(
        id=501,
        status=TaskStatus.PENDING,
        started_at=None,
        finished_at=None,
        result_summary=None,
        error_message=None,
    )
    agent = SimpleNamespace(
        code="backend",
        adapter_type="qwen",
        system_prompt="You are the backend engineer.",
    )
    db = FakeExecutorDb(child_task)
    adapter_requests = []
    events = []
    logs = []

    class FakeAdapter:
        async def run(self, request):
            adapter_requests.append(request)
            return adapter_result

    async def fake_broadcast_task_event(task, event_name):
        events.append((task.status, event_name))

    async def fake_broadcast_task_log(task, message):
        logs.append(message)

    def forbidden_llm_factory():
        raise AssertionError("execute_node must not call the LLM directly")

    monkeypatch.setattr(nodes, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        nodes,
        "get_chat_llm",
        forbidden_llm_factory,
        raising=False,
    )
    monkeypatch.setattr(
        task_service,
        "get_or_create_agent",
        lambda actual_db, agent_code: agent,
    )
    monkeypatch.setattr(
        task_service,
        "get_adapter",
        lambda actual_agent: FakeAdapter(),
    )
    monkeypatch.setattr(
        task_service,
        "broadcast_task_event",
        fake_broadcast_task_event,
    )
    monkeypatch.setattr(
        task_service,
        "broadcast_task_log",
        fake_broadcast_task_log,
    )

    return SimpleNamespace(
        child_task=child_task,
        agent=agent,
        db=db,
        adapter_requests=adapter_requests,
        events=events,
        logs=logs,
    )


@pytest.mark.anyio
async def test_execute_node_delegates_child_execution_to_adapter(monkeypatch):
    observed = _install_executor_fakes(
        monkeypatch,
        AgentRunResult(
            status="success",
            summary="Implemented the endpoint.",
            changed_files=["app/api/example.py"],
        ),
    )

    result = await nodes.execute_node(
        _executor_state(
            observed.child_task.id,
            errors=["Previous execution error"],
        )
    )

    assert len(observed.adapter_requests) == 1
    request = observed.adapter_requests[0]
    assert request.task_id == observed.child_task.id
    assert request.conversation_id == 7
    assert request.instruction == "Implement the API endpoint."
    assert request.repo_path == "/trusted/workspace"
    assert request.task is observed.child_task
    assert request.context == {
        "agent_code": "backend",
        "system_prompt": "You are the backend engineer.",
        "previous_results": [],
        "previous_errors": ["Previous execution error"],
        "plan_step_index": 0,
        "parent_task_id": 100,
        "verification_results": [],
        "changed_files": [],
        "previous_error": "Previous execution error",
    }

    assert observed.child_task.status == TaskStatus.SUCCESS
    assert observed.child_task.result_summary == "Implemented the endpoint."
    assert observed.child_task.finished_at is not None
    assert observed.db.closed is True
    assert observed.events == [
        (TaskStatus.RUNNING, "task.updated"),
        (TaskStatus.SUCCESS, "task.updated"),
    ]
    assert "Detected previous errors" in observed.logs[1]

    assert result["execution_results"] == [
        {
            "step": 0,
            "content": "Implemented the endpoint.",
            "files": ["app/api/example.py"],
        }
    ]
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content == "Implemented the endpoint."
    assert result["errors"] == []


@pytest.mark.anyio
async def test_execute_node_marks_child_failed_for_unsuccessful_adapter_result(
    monkeypatch,
):
    observed = _install_executor_fakes(
        monkeypatch,
        AgentRunResult(
            status="failed",
            summary="Adapter execution failed.",
        ),
    )

    result = await nodes.execute_node(
        _executor_state(observed.child_task.id)
    )

    assert observed.child_task.status == TaskStatus.FAILED
    assert observed.child_task.error_message == "Adapter execution failed."
    assert observed.child_task.finished_at is not None
    assert observed.db.closed is True
    assert result == {
        "errors": ["Execution error: Adapter execution failed."]
    }
