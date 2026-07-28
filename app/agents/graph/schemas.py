import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError, field_validator


logger = logging.getLogger(__name__)


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
                return result
            return OrchestratorPlan.model_validate(result)
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

    return OrchestratorPlan(
        steps=[
            PlanStep(
                agent="backend",
                instruction=user_goal.strip() or "Handle the user request.",
            )
        ]
    )
