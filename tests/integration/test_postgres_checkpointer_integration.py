import asyncio
import os
import sys
from typing import TypedDict
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph


POSTGRES_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="AGENTHUB_TEST_POSTGRES_DSN is not configured",
)


class CounterState(TypedDict):
    value: int


def run_async(coroutine):
    if sys.platform == "win32":
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop,
        ) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)


def test_postgres_checkpointer_resumes_thread_across_independent_connections():
    async def increment(state: CounterState) -> CounterState:
        return {"value": state["value"] + 1}

    workflow = StateGraph(CounterState)
    workflow.add_node("increment", increment)
    workflow.add_edge(START, "increment")
    workflow.add_edge("increment", END)
    config = {
        "configurable": {
            "thread_id": f"postgres-integration-{uuid4().hex}",
        }
    }

    async def exercise():
        async with AsyncPostgresSaver.from_conn_string(
            POSTGRES_DSN
        ) as first_saver:
            await first_saver.setup()
            first_graph = workflow.compile(checkpointer=first_saver)
            await first_graph.ainvoke({"value": 41}, config)

        async with AsyncPostgresSaver.from_conn_string(
            POSTGRES_DSN
        ) as second_saver:
            second_graph = workflow.compile(checkpointer=second_saver)
            snapshot = await second_graph.aget_state(config)
            return snapshot.values

    assert run_async(exercise()) == {"value": 42}
