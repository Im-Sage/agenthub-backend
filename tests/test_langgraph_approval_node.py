import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from app.agents.graph import nodes
from app.agents.graph.state import AgentState
from app.schemas.enums import TaskStatus


class FakeDb:
    def __init__(self, parent_task):
        self.parent_task = parent_task
        self.commits = 0

    def get(self, model, task_id):
        assert task_id == self.parent_task.id
        return self.parent_task

    def commit(self):
        self.commits += 1

    def close(self):
        return None


def install_plan_node_fakes(monkeypatch, parent_metadata, generated_plan):
    from app.services import task_service

    parent_task = SimpleNamespace(
        id=42,
        conversation_id=7,
        metadata_json=json.dumps(parent_metadata),
        status=TaskStatus.RUNNING,
        result_summary=None,
        finished_at=object(),
    )
    fake_db = FakeDb(parent_task)
    llm_calls = []
    child_tasks = []
    events = []

    class FakeLlm:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            llm_calls.append(messages)
            return {"steps": generated_plan}

    def fake_create_subtask(
        db,
        parent,
        agent_code,
        instruction,
        task_type=None,
        depends_on=None,
        step_key=None,
        step_index=None,
        write_scope=None,
    ):
        child_task = SimpleNamespace(
            id=900 + len(child_tasks),
            agent_code=agent_code,
            instruction=instruction,
            task_type=task_type,
            depends_on=depends_on,
            step_key=step_key,
            step_index=step_index,
            write_scope=write_scope,
        )
        child_tasks.append(child_task)
        return child_task

    async def fake_broadcast_task_event(task, event_name):
        events.append((task, event_name))

    monkeypatch.setattr(nodes, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(nodes, "get_chat_llm", lambda: FakeLlm())
    monkeypatch.setattr(task_service, "create_subtask", fake_create_subtask)
    monkeypatch.setattr(
        task_service,
        "broadcast_task_event",
        fake_broadcast_task_event,
    )

    state = {
        "task_id": parent_task.id,
        "messages": [HumanMessage(content="Build the new system")],
    }
    result = asyncio.run(nodes.plan_node(state))
    return SimpleNamespace(
        parent_task=parent_task,
        db=fake_db,
        llm_calls=llm_calls,
        child_tasks=child_tasks,
        events=events,
        result=result,
    )


def test_agent_state_declares_approval_status():
    assert AgentState.__annotations__["approval_status"] == str | None


def test_plan_node_does_not_reuse_confirmed_database_plan(monkeypatch):
    old_plan = [
        {
            "id": "old",
            "agent": "frontend",
            "instruction": "Old plan",
            "depends_on": [],
            "write_scope": ["agenthub-frontend"],
        }
    ]
    new_plan = [
        {
            "id": "new",
            "agent": "backend",
            "instruction": "New plan",
            "depends_on": [],
            "write_scope": ["app"],
        }
    ]

    observed = install_plan_node_fakes(
        monkeypatch,
        {
            "plan_status": "confirmed",
            "plan": old_plan,
            "child_ids": [100],
        },
        new_plan,
    )

    assert len(observed.llm_calls) == 1
    assert observed.result["plan"] == new_plan
    assert [child.id for child in observed.child_tasks] == [900]


def test_plan_node_leaves_parent_and_graph_waiting_for_approval(monkeypatch):
    plan = [
        {
            "id": "persistence",
            "agent": "backend",
            "instruction": "Implement persistence",
            "depends_on": [],
            "write_scope": ["app/models", "alembic"],
        }
    ]

    observed = install_plan_node_fakes(monkeypatch, {}, plan)

    metadata = json.loads(observed.parent_task.metadata_json)
    assert observed.parent_task.status == TaskStatus.PENDING
    assert observed.parent_task.finished_at is None
    assert metadata["plan_status"] == "awaiting_confirmation"
    assert metadata["child_ids"] == [900]
    assert observed.child_tasks[0].step_key == "persistence"
    assert observed.child_tasks[0].step_index == 0
    assert observed.child_tasks[0].depends_on == []
    assert observed.child_tasks[0].write_scope == ["app/models", "alembic"]
    assert observed.result == {
        "plan": plan,
        "current_step_index": 0,
        "current_agent": "backend",
        "current_instruction": "Implement persistence",
        "metadata_json": json.dumps({"child_ids": [900]}),
        "awaiting_confirmation": True,
        "approval_status": None,
        "errors": [],
        "is_finished": False,
        "final_summary": None,
    }


@pytest.mark.parametrize(
    ("resume_value", "expected_status", "expected_finished"),
    [
        ({"approved": True}, "approved", False),
        ({"approved": False}, "rejected", True),
        (True, "approved", False),
        (False, "rejected", True),
    ],
)
def test_approval_node_consumes_resume_value_without_side_effects(
    monkeypatch,
    resume_value,
    expected_status,
    expected_finished,
):
    from app.agents.graph.nodes import approval_node

    interrupt_payloads = []

    def fake_interrupt(payload):
        interrupt_payloads.append(payload)
        return resume_value

    def forbidden_side_effect():
        raise AssertionError("approval_node touched a side-effecting dependency")

    monkeypatch.setattr(nodes, "interrupt", fake_interrupt)
    monkeypatch.setattr(nodes, "SessionLocal", forbidden_side_effect)
    monkeypatch.setattr(nodes, "get_chat_llm", forbidden_side_effect)

    plan = [
        {
            "id": "persistence",
            "agent": "backend",
            "instruction": "Implement persistence",
            "depends_on": [],
            "write_scope": ["app/models"],
        }
    ]
    result = asyncio.run(
        approval_node(
            {
                "task_id": 42,
                "plan": plan,
                "awaiting_confirmation": True,
            }
        )
    )

    assert interrupt_payloads == [
        {
            "type": "orchestrator_plan_approval",
            "task_id": 42,
            "plan": plan,
        }
    ]
    assert result == {
        "approval_status": expected_status,
        "awaiting_confirmation": False,
        "is_finished": expected_finished,
    }
