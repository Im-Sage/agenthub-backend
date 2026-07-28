import logging
import time
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError, field_validator
from app.core.logging import get_logger, log_agent_event


logger = get_logger("planner")


class PlanStep(BaseModel):
    agent: Literal["backend", "frontend", "reviewer"]
    instruction: str

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        instruction = value.strip()
        if not instruction:
            raise ValueError("instruction must not be empty")
        return instruction


class OrchestratorPlan(BaseModel):
    steps: list[PlanStep] = Field(min_length=1, max_length=12)


class VerificationCheck(BaseModel):
    name: str
    success: bool
    exit_code: int | None
    summary: str
    duration_ms: int


class VerificationResult(BaseModel):
    success: bool
    checks: list[VerificationCheck]
    failure_summary: str | None = None


async def generate_orchestrator_plan(
    llm: Any,
    user_goal: str,
    *,
    max_attempts: int = 2,
) -> OrchestratorPlan:
    started = time.perf_counter()
    log_agent_event(
        logger,
        "planner.started",
        agent_code="orchestrator",
        success=None,
    )
    messages = [
        SystemMessage(
            content=(
                "You are a software task orchestrator. Create a structured plan "
                "with one to twelve steps. Each step must select exactly one of "
                "backend, frontend, or reviewer and include a concrete instruction."
            )
        ),
        HumanMessage(content=f"User goal: {user_goal}"),
    ]
    structured_llm = llm.with_structured_output(OrchestratorPlan)

    for attempt in range(max(0, max_attempts)):
        try:
            result = await structured_llm.ainvoke(messages)
            if isinstance(result, OrchestratorPlan):
                log_agent_event(
                    logger,
                    "planner.completed",
                    agent_code="orchestrator",
                    duration_ms=int(
                        (time.perf_counter() - started) * 1000
                    ),
                    success=True,
                    step_count=len(result.steps),
                )
                return result
            validated = OrchestratorPlan.model_validate(result)
            log_agent_event(
                logger,
                "planner.completed",
                agent_code="orchestrator",
                duration_ms=int(
                    (time.perf_counter() - started) * 1000
                ),
                success=True,
                step_count=len(validated.steps),
            )
            return validated
        except Exception as exc:
            validation_errors = (
                exc.errors(include_input=False)
                if isinstance(exc, ValidationError)
                else None
            )
            logger.warning(
                "Planner structured output failed: attempt=%s error_type=%s "
                "validation_errors=%s",
                attempt + 1,
                type(exc).__name__,
                validation_errors,
            )

    fallback = OrchestratorPlan(
        steps=[
            PlanStep(
                agent="backend",
                instruction=user_goal.strip() or "Handle the user request.",
            )
        ]
    )
    log_agent_event(
        logger,
        "planner.fallback",
        agent_code="orchestrator",
        duration_ms=int((time.perf_counter() - started) * 1000),
        success=True,
        fallback_reason="structured_output_failed",
        step_count=1,
    )
    return fallback
