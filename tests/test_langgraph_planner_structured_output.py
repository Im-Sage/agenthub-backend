import asyncio

import pytest
from pydantic import ValidationError

from app.agents.graph.schemas import (
    OrchestratorPlan,
    PlanStep,
    generate_orchestrator_plan,
)


def test_orchestrator_plan_accepts_a_valid_plan():
    plan = OrchestratorPlan(
        steps=[
            PlanStep(
                id="backend-plan",
                agent="backend",
                instruction="  Implement structured planning.  ",
                depends_on=[],
                write_scope=["app/agents/graph"],
            )
        ]
    )

    assert plan.steps[0].instruction == "Implement structured planning."


@pytest.mark.parametrize(
    "payload",
    [
        {"steps": []},
        {"steps": [{"agent": "backend", "instruction": "   "}]},
        {"steps": [{"agent": "designer", "instruction": "Create UI"}]},
        {
            "steps": [
                {"agent": "backend", "instruction": f"Step {index}"}
                for index in range(13)
            ]
        },
    ],
)
def test_orchestrator_plan_rejects_invalid_structure(payload):
    with pytest.raises(ValidationError):
        OrchestratorPlan.model_validate(payload)


class FakeStructuredLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.schema = None
        self.calls = 0
        self.messages = []

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_generate_orchestrator_plan_retries_after_validation_failure():
    llm = FakeStructuredLlm(
        [
            {"steps": []},
            {
                "steps": [
                    {
                        "id": "review",
                        "agent": "reviewer",
                        "instruction": "Review the implementation",
                        "depends_on": [],
                        "write_scope": [],
                    }
                ]
            },
        ]
    )

    plan = asyncio.run(
        generate_orchestrator_plan(llm, "Harden the agent")
    )

    assert llm.schema is OrchestratorPlan
    assert llm.calls == 2
    system_prompt = str(llm.messages[0][0].content)
    assert all(
        field_name in system_prompt
        for field_name in (
            "id",
            "agent",
            "instruction",
            "depends_on",
            "write_scope",
        )
    )
    assert plan == OrchestratorPlan(
        steps=[
            PlanStep(
                id="review",
                agent="reviewer",
                instruction="Review the implementation",
                depends_on=[],
                write_scope=[],
            )
        ]
    )


def test_generate_orchestrator_plan_falls_back_after_two_failures():
    llm = FakeStructuredLlm([{"steps": []}, RuntimeError("provider failed")])

    plan = asyncio.run(
        generate_orchestrator_plan(llm, "  Repair the backend  ")
    )

    assert llm.calls == 2
    assert plan == OrchestratorPlan(
        steps=[
            PlanStep(
                id="step-1",
                agent="backend",
                instruction="Repair the backend",
                depends_on=[],
                write_scope=["**"],
            )
        ]
    )


def test_generate_orchestrator_plan_returns_first_valid_result():
    llm = FakeStructuredLlm(
        [
            OrchestratorPlan(
                steps=[
                    PlanStep(
                        id="frontend",
                        agent="frontend",
                        instruction="Build the interface",
                        depends_on=[],
                        write_scope=["agenthub-frontend"],
                    )
                ]
            )
        ]
    )

    plan = asyncio.run(
        generate_orchestrator_plan(llm, "Build the interface")
    )

    assert llm.calls == 1
    assert plan.steps[0].agent == "frontend"
