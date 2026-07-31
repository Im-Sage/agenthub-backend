import pytest

from app.agents.graph.schemas import PlanStep
from app.services.orchestrator_schedule_service import (
    ExecutionWave,
    build_execution_waves,
    scopes_overlap,
)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (["**"], ["app/api"], True),
        ([], ["**"], False),
        ([], [], False),
        (["app/api"], ["app/api"], True),
        (["app"], ["app/api/routes.py"], True),
        (["app/api"], ["app/models"], False),
        (["app/api/*.py"], ["app/api/routes.ts"], True),
        (["app/service-*.py"], ["app/service-users.py"], True),
        (["app/service-*.py"], ["app/models.py"], False),
        (["../outside"], ["docs"], True),
    ],
)
def test_scopes_overlap_is_conservative(left, right, expected):
    assert scopes_overlap(left, right) is expected
    assert scopes_overlap(right, left) is expected


def make_step(
    step_id,
    *,
    depends_on=None,
    write_scope=None,
    agent="backend",
):
    return PlanStep(
        id=step_id,
        agent=agent,
        instruction=f"Execute {step_id}.",
        depends_on=list(depends_on or []),
        write_scope=(
            list(write_scope)
            if write_scope is not None
            else ["**"]
        ),
    )


def test_build_execution_waves_parallelizes_disjoint_ready_steps():
    steps = [
        make_step("backend", write_scope=["app/api"]),
        make_step(
            "frontend",
            agent="frontend",
            write_scope=["agenthub-frontend"],
        ),
        make_step(
            "review",
            agent="reviewer",
            depends_on=["backend", "frontend"],
            write_scope=[],
        ),
    ]

    assert build_execution_waves(steps) == [
        ExecutionWave(index=0, step_ids=("backend", "frontend")),
        ExecutionWave(index=1, step_ids=("review",)),
    ]


def test_build_execution_waves_serializes_overlapping_ready_steps_stably():
    steps = [
        make_step("first", write_scope=["app"]),
        make_step("second", write_scope=["app/api"]),
        make_step("third", write_scope=["docs"]),
    ]

    assert build_execution_waves(steps) == [
        ExecutionWave(index=0, step_ids=("first", "third")),
        ExecutionWave(index=1, step_ids=("second",)),
    ]


def test_read_only_step_can_share_wave_with_writer():
    steps = [
        make_step("writer", write_scope=["**"]),
        make_step("reader", agent="reviewer", write_scope=[]),
    ]

    assert build_execution_waves(steps) == [
        ExecutionWave(index=0, step_ids=("writer", "reader")),
    ]
