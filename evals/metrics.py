from collections.abc import Iterable, Sequence


def success_rate(outcomes: Iterable[bool]) -> float:
    values = list(outcomes)
    if not values:
        return 0.0
    return sum(bool(value) for value in values) / len(values)


def average(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(items) / len(items)


def retrieval_recall_at_k(
    expected_paths: Sequence[str],
    actual_paths: Sequence[str],
    *,
    k: int = 5,
) -> float:
    expected = set(expected_paths)
    if not expected:
        return 1.0
    retrieved = set(actual_paths[:k])
    return len(expected & retrieved) / len(expected)


def reciprocal_rank(
    expected_paths: Sequence[str],
    actual_paths: Sequence[str],
) -> float:
    expected = set(expected_paths)
    for rank, path in enumerate(actual_paths, start=1):
        if path in expected:
            return 1.0 / rank
    return 0.0


OFFLINE_THRESHOLDS = {
    "planner_schema_success_rate": ("eq", 1.0),
    "planner_dag_validity_rate": ("eq", 1.0),
    "planner_scope_validity_rate": ("eq", 1.0),
    "retrieval_recall_at_5": ("min", 0.80),
    "context_truncation_rate": ("max", 0.30),
    "tool_call_success_rate": ("eq", 1.0),
    "verification_pass_rate": ("eq", 1.0),
}


def threshold_failures(
    metrics: dict[str, float],
    *,
    required_metrics: set[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    for name, (operator, threshold) in OFFLINE_THRESHOLDS.items():
        if required_metrics is not None and name not in required_metrics:
            continue
        value = metrics.get(name)
        if value is None:
            failures.append(f"{name}: missing")
        elif operator == "eq" and value != threshold:
            failures.append(f"{name}: {value:.4f} != {threshold:.4f}")
        elif operator == "min" and value < threshold:
            failures.append(f"{name}: {value:.4f} < {threshold:.4f}")
        elif operator == "max" and value > threshold:
            failures.append(f"{name}: {value:.4f} > {threshold:.4f}")
    return failures
