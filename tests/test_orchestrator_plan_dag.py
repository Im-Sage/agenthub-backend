import pytest
from pydantic import ValidationError

from app.agents.graph.schemas import OrchestratorPlan, PlanStep


def test_valid_dag_normalizes_write_scopes_to_posix_paths():
    plan = OrchestratorPlan(
        steps=[
            PlanStep(
                id="backend-api",
                agent="backend",
                instruction="Implement the API.",
                depends_on=[],
                write_scope=[r"app\api", "./tests/api/*.py"],
            ),
            PlanStep(
                id="review",
                agent="reviewer",
                instruction="Review the merged implementation.",
                depends_on=["backend-api"],
                write_scope=[],
            ),
        ]
    )

    assert plan.steps[0].write_scope == ["app/api", "tests/api/*.py"]
    assert plan.steps[1].write_scope == []


def test_write_scope_defaults_to_conservative_global_scope():
    step = PlanStep(
        id="backend",
        agent="backend",
        instruction="Implement the change.",
    )

    assert step.depends_on == []
    assert step.write_scope == ["**"]


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        (
            [
                {
                    "id": "same",
                    "agent": "backend",
                    "instruction": "First.",
                },
                {
                    "id": "same",
                    "agent": "frontend",
                    "instruction": "Second.",
                },
            ],
            "unique",
        ),
        (
            [
                {
                    "id": "backend",
                    "agent": "backend",
                    "instruction": "Implement.",
                    "depends_on": ["missing"],
                }
            ],
            "unknown",
        ),
        (
            [
                {
                    "id": "backend",
                    "agent": "backend",
                    "instruction": "Implement.",
                    "depends_on": ["backend"],
                }
            ],
            "itself",
        ),
        (
            [
                {
                    "id": "backend",
                    "agent": "backend",
                    "instruction": "Implement.",
                    "depends_on": ["frontend"],
                },
                {
                    "id": "frontend",
                    "agent": "frontend",
                    "instruction": "Integrate.",
                    "depends_on": ["backend"],
                },
            ],
            "cycle",
        ),
    ],
)
def test_orchestrator_plan_rejects_invalid_dependency_graph(steps, message):
    with pytest.raises(ValidationError, match=message):
        OrchestratorPlan.model_validate({"steps": steps})


@pytest.mark.parametrize(
    "scope",
    [
        "/absolute/path",
        r"C:\absolute\path",
        "../outside",
        "app/../outside",
        ".git/config",
        "app/.git/index",
        "app/\x00secret",
        "",
    ],
)
def test_plan_step_rejects_unsafe_write_scope(scope):
    with pytest.raises(ValidationError):
        PlanStep(
            id="backend",
            agent="backend",
            instruction="Implement safely.",
            write_scope=[scope],
        )


def test_plan_step_rejects_more_than_thirty_two_write_scopes():
    with pytest.raises(ValidationError):
        PlanStep(
            id="backend",
            agent="backend",
            instruction="Implement safely.",
            write_scope=[f"app/module-{index}" for index in range(33)],
        )
