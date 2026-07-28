import asyncio
import json

from evals.metrics import (
    average,
    reciprocal_rank,
    retrieval_recall_at_k,
    success_rate,
    threshold_failures,
)
from evals.report import markdown_report, write_report
from evals.run import CASES_ROOT, load_jsonl, run_evaluation


def test_dataset_minimum_sizes_and_required_fields():
    planner = load_jsonl(CASES_ROOT / "planner_cases.jsonl")
    retrieval = load_jsonl(CASES_ROOT / "retrieval_cases.jsonl")

    assert len(planner) >= 20
    assert len(retrieval) >= 15
    assert all(
        {
            "id",
            "instruction",
            "expected_agents",
            "min_steps",
            "max_steps",
        }
        <= set(case)
        for case in planner
    )
    assert all(
        {"id", "query", "expected_paths"} <= set(case)
        for case in retrieval
    )


def test_pure_rate_and_average_metrics():
    assert success_rate([True, False, True]) == 2 / 3
    assert success_rate([]) == 0.0
    assert average([1.0, 2.0, 3.0]) == 2.0
    assert average([]) == 0.0


def test_retrieval_recall_at_five_and_mrr():
    expected = ["a.py", "b.py"]
    actual = ["x.py", "b.py", "a.py", "z.py", "later.py", "no.py"]

    assert retrieval_recall_at_k(expected, actual, k=2) == 0.5
    assert retrieval_recall_at_k(expected, actual, k=5) == 1.0
    assert reciprocal_rank(expected, actual) == 0.5
    assert reciprocal_rank(["missing.py"], actual) == 0.0


def test_threshold_failures_enforce_offline_gate():
    passing = {
        "planner_schema_success_rate": 1.0,
        "retrieval_recall_at_5": 0.8,
        "context_truncation_rate": 0.3,
        "tool_call_success_rate": 1.0,
        "verification_pass_rate": 1.0,
    }
    assert threshold_failures(passing) == []

    failing = {**passing, "retrieval_recall_at_5": 0.79}
    assert threshold_failures(failing) == [
        "retrieval_recall_at_5: 0.7900 < 0.8000"
    ]


def test_json_and_markdown_reports_include_failures(tmp_path):
    report = {
        "mode": "offline",
        "gate_passed": False,
        "metrics": {"retrieval_recall_at_5": 0.5},
        "threshold_failures": ["recall too low"],
        "failed_cases": [
            {
                "id": "retrieval-001",
                "type": "retrieval",
                "reason": "missing",
                "expected": ["expected.py"],
                "actual": ["actual.py"],
            }
        ],
    }

    json_path, markdown_path = write_report(
        report,
        tmp_path / "report.json",
    )

    assert json.loads(json_path.read_text(encoding="utf-8")) == report
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "retrieval-001" in markdown
    assert "expected.py" in markdown
    assert "actual.py" in markdown
    assert markdown == markdown_report(report)


def test_live_mode_without_key_is_a_clear_non_failure(monkeypatch):
    monkeypatch.delenv("ALIYUN_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    report = asyncio.run(run_evaluation("live"))

    assert report["status"] == "skipped"
    assert report["gate_passed"] is True
    assert "requires a configured API key" in report["reason"]
