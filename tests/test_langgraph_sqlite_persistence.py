import asyncio
from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.agents.graph import workflow
from app.agents.graph.runtime import graph_config


def initial_state(task_id: int) -> dict:
    return {
        "messages": [HumanMessage(content="Prove durable resume")],
        "task_id": task_id,
        "conversation_id": 7,
        "repo_path": None,
        "plan": [],
        "current_step_index": 0,
        "current_agent": None,
        "current_instruction": None,
        "execution_results": [],
        "errors": [],
        "awaiting_confirmation": False,
        "approval_status": None,
        "is_finished": False,
        "final_summary": None,
        "metadata_json": None,
    }


def install_fake_workflow_nodes(monkeypatch):
    calls = {
        "planner": [],
        "executor": [],
        "verifier": [],
        "summarizer": [],
    }

    async def fake_planner(state):
        calls["planner"].append(state["task_id"])
        return {
            "plan": [
                {
                    "agent": "backend",
                    "instruction": "Resume from SQLite checkpoint",
                }
            ],
            "current_step_index": 0,
            "current_agent": "backend",
            "current_instruction": "Resume from SQLite checkpoint",
            "metadata_json": '{"child_ids": [901]}',
            "awaiting_confirmation": True,
            "approval_status": None,
            "errors": [],
            "is_finished": False,
            "final_summary": None,
        }

    async def fake_executor(state):
        calls["executor"].append(state["task_id"])
        return {
            "execution_results": [
                {
                    "step": state["current_step_index"],
                    "content": "resumed execution",
                    "files": [],
                }
            ],
            "errors": [],
        }

    async def fake_verifier(state):
        calls["verifier"].append(state["task_id"])
        return {
            "current_step_index": 1,
            "current_agent": None,
            "current_instruction": None,
            "is_finished": True,
            "errors": [],
        }

    async def fake_summarizer(state):
        calls["summarizer"].append(state["task_id"])
        return {"final_summary": "resumed after checkpointer reopen"}

    monkeypatch.setattr(workflow, "plan_node", fake_planner)
    monkeypatch.setattr(workflow, "execute_node", fake_executor)
    monkeypatch.setattr(workflow, "verify_node", fake_verifier)
    monkeypatch.setattr(workflow, "summarize_node", fake_summarizer)
    return SimpleNamespace(calls=calls)


def test_sqlite_checkpoint_resumes_after_saver_reopen(monkeypatch, tmp_path):
    observed = install_fake_workflow_nodes(monkeypatch)
    checkpoint_path = (tmp_path / "durable-resume.sqlite3").as_posix()
    task_id = 808
    config = graph_config(task_id)

    async def exercise_reopen():
        async with AsyncSqliteSaver.from_conn_string(
            checkpoint_path
        ) as first_saver:
            first_graph = workflow.create_agent_graph(
                checkpointer=first_saver
            )
            interrupted = await first_graph.ainvoke(
                initial_state(task_id),
                config=config,
            )

            assert interrupted["__interrupt__"]
            assert interrupted["awaiting_confirmation"] is True
            assert observed.calls["planner"] == [task_id]
            assert observed.calls["executor"] == []

        async with AsyncSqliteSaver.from_conn_string(
            checkpoint_path
        ) as second_saver:
            assert second_saver is not first_saver
            second_graph = workflow.create_agent_graph(
                checkpointer=second_saver
            )
            resumed = await second_graph.ainvoke(
                Command(resume={"approved": True}),
                config=config,
            )

        return resumed

    resumed = asyncio.run(exercise_reopen())

    assert observed.calls["planner"] == [task_id]
    assert observed.calls["executor"] == [task_id]
    assert observed.calls["verifier"] == [task_id]
    assert observed.calls["summarizer"] == [task_id]
    assert resumed["approval_status"] == "approved"
    assert resumed["final_summary"] == "resumed after checkpointer reopen"
