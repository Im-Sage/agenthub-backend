import logging
import re
import time
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from app.core.logging import get_logger, log_agent_event


logger = get_logger("planner")


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    agent: Literal["backend", "frontend", "reviewer"]
    instruction: str
    depends_on: list[str] = Field(default_factory=list)
    write_scope: list[str] = Field(
        default_factory=lambda: ["**"],
        max_length=32,
    )

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        instruction = value.strip()
        if not instruction:
            raise ValueError("instruction must not be empty")
        return instruction

    @field_validator("depends_on")
    @classmethod
    def validate_dependencies(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("depends_on must not contain duplicates")
        return value

    @field_validator("write_scope")
    @classmethod
    def normalize_write_scope(cls, value: list[str]) -> list[str]:
        normalized_scopes: list[str] = []
        for raw_scope in value:
            if not isinstance(raw_scope, str):
                raise ValueError("write_scope entries must be strings")
            if "\x00" in raw_scope:
                raise ValueError("write_scope must not contain NUL")

            scope = raw_scope.strip().replace("\\", "/")
            while scope.startswith("./"):
                scope = scope[2:]
            if (
                not scope
                or scope.startswith("/")
                or re.match(r"^[A-Za-z]:", scope)
            ):
                raise ValueError(
                    "write_scope entries must be relative repository paths"
                )

            parts = [part for part in scope.split("/") if part not in ("", ".")]
            if any(part == ".." for part in parts):
                raise ValueError("write_scope must not traverse parent paths")
            if any(part.casefold() == ".git" for part in parts):
                raise ValueError("write_scope must not target .git")

            normalized = "/".join(parts)
            if not normalized:
                raise ValueError("write_scope entries must not be empty")
            normalized_scopes.append(normalized)
        return normalized_scopes


class OrchestratorPlan(BaseModel):
    steps: list[PlanStep] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_dependency_graph(self):
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step ids must be unique")

        known_ids = set(step_ids)
        dependencies = {
            step.id: tuple(step.depends_on)
            for step in self.steps
        }
        for step in self.steps:
            if step.id in step.depends_on:
                raise ValueError(f"step {step.id!r} cannot depend on itself")
            unknown = [
                dependency
                for dependency in step.depends_on
                if dependency not in known_ids
            ]
            if unknown:
                raise ValueError(
                    f"step {step.id!r} has unknown dependencies: {unknown}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("orchestrator plan contains a dependency cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)
        return self


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
                "backend, frontend, or reviewer and include all five fields: "
                "id, agent, instruction, depends_on, and write_scope. Use a unique "
                "lowercase id, list only earlier or otherwise acyclic step ids in "
                "depends_on, and provide conservative POSIX-relative write_scope "
                "patterns. Use an empty write_scope only for read-only work."
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
                id="step-1",
                agent="backend",
                instruction=user_goal.strip() or "Handle the user request.",
                depends_on=[],
                write_scope=["**"],
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
