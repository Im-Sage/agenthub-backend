import re
from dataclasses import dataclass

from app.agents.graph.schemas import PlanStep


_WILDCARD_CHARACTERS = frozenset("*?[")


@dataclass(frozen=True)
class ExecutionWave:
    index: int
    step_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ScopePrefix:
    value: str
    wildcard: bool
    universal: bool = False


def _scope_prefix(raw_scope: object) -> _ScopePrefix:
    if not isinstance(raw_scope, str) or "\x00" in raw_scope:
        return _ScopePrefix("", wildcard=True, universal=True)

    scope = raw_scope.strip().replace("\\", "/")
    while scope.startswith("./"):
        scope = scope[2:]
    parts = [part for part in scope.split("/") if part not in ("", ".")]
    if (
        not scope
        or scope.startswith("/")
        or re.match(r"^[A-Za-z]:", scope)
        or any(part == ".." for part in parts)
        or any(part.casefold() == ".git" for part in parts)
    ):
        return _ScopePrefix("", wildcard=True, universal=True)

    normalized = "/".join(parts)
    if normalized == "**":
        return _ScopePrefix("", wildcard=True, universal=True)

    wildcard_positions = [
        normalized.find(character)
        for character in _WILDCARD_CHARACTERS
        if character in normalized
    ]
    if not wildcard_positions:
        return _ScopePrefix(normalized, wildcard=False)

    literal_prefix = normalized[: min(wildcard_positions)].rstrip("/")
    if not literal_prefix:
        return _ScopePrefix("", wildcard=True, universal=True)
    return _ScopePrefix(literal_prefix, wildcard=True)


def _prefixes_overlap(left: _ScopePrefix, right: _ScopePrefix) -> bool:
    if left.universal or right.universal:
        return True
    if left.wildcard or right.wildcard:
        return (
            left.value.startswith(right.value)
            or right.value.startswith(left.value)
        )
    return (
        left.value == right.value
        or left.value.startswith(f"{right.value}/")
        or right.value.startswith(f"{left.value}/")
    )


def scopes_overlap(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    left_prefixes = [_scope_prefix(scope) for scope in left]
    right_prefixes = [_scope_prefix(scope) for scope in right]
    return any(
        _prefixes_overlap(left_prefix, right_prefix)
        for left_prefix in left_prefixes
        for right_prefix in right_prefixes
    )


def build_execution_waves(steps: list[PlanStep]) -> list[ExecutionWave]:
    step_ids = [step.id for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("step ids must be unique")

    known_ids = set(step_ids)
    for step in steps:
        unknown = set(step.depends_on) - known_ids
        if unknown:
            raise ValueError(
                f"step {step.id!r} has unknown dependencies: "
                f"{sorted(unknown)}"
            )

    remaining = list(steps)
    completed: set[str] = set()
    waves: list[ExecutionWave] = []
    while remaining:
        selected: list[PlanStep] = []
        for step in remaining:
            if not set(step.depends_on) <= completed:
                continue
            if any(
                scopes_overlap(step.write_scope, other.write_scope)
                for other in selected
            ):
                continue
            selected.append(step)

        if not selected:
            raise ValueError("orchestrator plan contains a dependency cycle")

        waves.append(
            ExecutionWave(
                index=len(waves),
                step_ids=tuple(step.id for step in selected),
            )
        )
        selected_ids = {step.id for step in selected}
        completed.update(selected_ids)
        remaining = [
            step
            for step in remaining
            if step.id not in selected_ids
        ]

    return waves
