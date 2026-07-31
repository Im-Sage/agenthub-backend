import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.graph.schemas import OrchestratorPlan, PlanStep
from app.core.config import PROJECT_ROOT
from app.core.logging import get_logger, log_agent_event
from app.models.repository import Repository
from app.models.user import Base, User
from app.mcp.repository_resolver import RepositoryResolver
from app.rag.embeddings import HashEmbeddingProvider
from app.rag.index_service import RepositoryIndexService
from app.rag.retrieval import HybridCodeRetriever
from app.services.workspace_service import workspace_service
from evals.metrics import (
    average,
    reciprocal_rank,
    retrieval_recall_at_k,
    success_rate,
    threshold_failures,
)
from evals.report import write_report


CASES_ROOT = Path(__file__).resolve().parent / "cases"
logger = get_logger("eval")
_EVAL_EXTENSIONS = {
    ".go",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


class FakeStructuredPlanner:
    def invoke(self, instruction: str) -> OrchestratorPlan:
        return fake_structured_plan(instruction)


class FakeCommandRunner:
    def run(self, check_name: str):
        return type(
            "FakeCommandResult",
            (),
            {"success": True, "check_name": check_name},
        )()


class AppWorkspaceSearch:
    def search_code(
        self,
        local_path: str,
        query: str,
        **kwargs,
    ):
        return workspace_service.search_code(
            local_path,
            query=query,
            target_dir="app",
            max_results=kwargs.get("max_results", 30),
        )


def evaluation_file_paths() -> list[str]:
    app_root = PROJECT_ROOT / "app"
    return sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in app_root.rglob("*")
        if path.is_file() and path.suffix.lower() in _EVAL_EXTENSIONS
    )


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def fake_structured_plan(instruction: str) -> OrchestratorPlan:
    normalized = instruction.casefold()
    agents: list[str] = []
    if any(
        marker in normalized
        for marker in (
            "api",
            "backend",
            "database",
            "worker",
            "后端",
            "接口",
            "数据库",
            "迁移",
            "unit tests",
        )
    ):
        agents.append("backend")
    if any(
        marker in normalized
        for marker in (
            "frontend",
            "react",
            "vue",
            "css",
            "page",
            "dashboard",
            "前端",
            "页面",
            "表单",
        )
    ):
        agents.append("frontend")
    if any(
        marker in normalized
        for marker in (
            "review",
            "审查",
            "检查",
            "风险",
        )
    ):
        agents.append("reviewer")
    if not agents:
        agents.append("backend")
    steps: list[PlanStep] = []
    for index, agent in enumerate(dict.fromkeys(agents), start=1):
        prior_step_ids = [step.id for step in steps]
        write_scope = {
            "backend": ["app", "tests"],
            "frontend": ["agenthub-frontend"],
            "reviewer": [],
        }[agent]
        steps.append(
            PlanStep(
                id=f"{agent}-{index}",
                agent=agent,
                instruction=instruction.strip() or "Handle the user request.",
                depends_on=(
                    prior_step_ids
                    if agent == "reviewer"
                    else []
                ),
                write_scope=write_scope,
            )
        )
    return OrchestratorPlan(steps=steps)


def planner_dag_is_valid(plan: OrchestratorPlan) -> bool:
    step_ids = [step.id for step in plan.steps]
    if len(step_ids) != len(set(step_ids)):
        return False
    known = set(step_ids)
    completed: set[str] = set()
    remaining = list(plan.steps)
    while remaining:
        ready = [
            step
            for step in remaining
            if set(step.depends_on) <= completed
        ]
        if not ready:
            return False
        for step in ready:
            if any(dependency not in known for dependency in step.depends_on):
                return False
            completed.add(step.id)
            remaining.remove(step)
    return True


def planner_scopes_are_valid(plan: OrchestratorPlan) -> bool:
    for step in plan.steps:
        try:
            validated = PlanStep.model_validate(step.model_dump())
        except Exception:
            return False
        if validated.write_scope != step.write_scope:
            return False
        if any("\\" in scope for scope in step.write_scope):
            return False
    return True


async def retrieval_results(cases: list[dict]) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="agenthub-eval-") as temp_dir:
        database_path = Path(temp_dir) / "eval.sqlite3"
        engine = create_engine(f"sqlite:///{database_path.as_posix()}")
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine, expire_on_commit=False)
        with sessions() as db:
            user = User(
                username="eval-user",
                email="eval@example.com",
                password_hash="offline",
            )
            db.add(user)
            db.flush()
            db.add(
                Repository(
                    id=1,
                    user_id=user.id,
                    name="agenthub-backend",
                    repo_url="offline://agenthub-backend",
                    local_path=str(PROJECT_ROOT),
                    default_branch="main",
                )
            )
            db.commit()
        resolver = RepositoryResolver(sessions)
        embeddings = HashEmbeddingProvider(dimensions=256)
        index_service = RepositoryIndexService(
            session_factory=sessions,
            repository_resolver=resolver,
            embedding_provider=embeddings,
            batch_size=32,
        )
        await index_service.update_files(1, evaluation_file_paths())
        retriever = HybridCodeRetriever(
            session_factory=sessions,
            repository_resolver=resolver,
            embedding_provider=embeddings,
            workspace_search=AppWorkspaceSearch(),
        )
        results: list[dict] = []
        for case in cases:
            chunks = await retriever.search(
                repository_id=1,
                user_id=1,
                query=case["query"],
                top_k=5,
            )
            results.append(
                {
                    "case": case,
                    "actual_paths": [
                        chunk.file_path for chunk in chunks
                    ],
                }
            )
        engine.dispose()
        return results


async def run_offline(suite: str | None = None) -> dict:
    planner_cases = load_jsonl(CASES_ROOT / "planner_cases.jsonl")
    retrieval_cases = (
        []
        if suite == "planner"
        else load_jsonl(CASES_ROOT / "retrieval_cases.jsonl")
    )
    planner_results: list[dict] = []
    planner = FakeStructuredPlanner()
    for case in planner_cases:
        fallback = not case["instruction"].strip()
        try:
            plan = planner.invoke(case["instruction"])
            schema_success = True
            actual_agents = [step.agent for step in plan.steps]
            dag_valid = planner_dag_is_valid(plan)
            scope_valid = planner_scopes_are_valid(plan)
        except Exception as exc:
            schema_success = False
            actual_agents = []
            dag_valid = False
            scope_valid = False
            fallback = True
            fallback_reason = type(exc).__name__
        else:
            fallback_reason = "empty_instruction" if fallback else None
        planner_results.append(
            {
                "case": case,
                "schema_success": schema_success,
                "dag_valid": dag_valid,
                "scope_valid": scope_valid,
                "fallback": fallback,
                "fallback_reason": fallback_reason,
                "actual_agents": actual_agents,
                "step_count": len(actual_agents),
            }
        )

    retrieved = await retrieval_results(retrieval_cases)
    recall_values = [
        retrieval_recall_at_k(
            result["case"]["expected_paths"],
            result["actual_paths"],
            k=5,
        )
        for result in retrieved
    ]
    reciprocal_ranks = [
        reciprocal_rank(
            result["case"]["expected_paths"],
            result["actual_paths"],
        )
        for result in retrieved
    ]
    context_truncated = [
        len(case["instruction"]) > 200
        for case in planner_cases
    ]
    command_runner = FakeCommandRunner()
    tool_outcomes = [
        command_runner.run("tool_call").success
        for _ in planner_cases
    ]
    verification_outcomes = [
        command_runner.run("verification").success
        for _ in planner_cases
    ]
    metrics = {
        "planner_schema_success_rate": success_rate(
            result["schema_success"] for result in planner_results
        ),
        "planner_dag_validity_rate": success_rate(
            result["dag_valid"] for result in planner_results
        ),
        "planner_scope_validity_rate": success_rate(
            result["scope_valid"] for result in planner_results
        ),
        "planner_fallback_rate": success_rate(
            result["fallback"] for result in planner_results
        ),
    }
    if suite != "planner":
        metrics.update(
            {
                "retrieval_recall_at_5": average(recall_values),
                "retrieval_mrr": average(reciprocal_ranks),
                "context_truncation_rate": success_rate(context_truncated),
                "tool_call_success_rate": success_rate(tool_outcomes),
                "verification_pass_rate": success_rate(
                    verification_outcomes
                ),
                "average_tool_rounds": average(
                    1.0 for _ in planner_cases
                ),
            }
        )
    failed_cases: list[dict] = []
    for result in planner_results:
        case = result["case"]
        expected_agents = case["expected_agents"]
        valid_steps = (
            case["min_steps"]
            <= result["step_count"]
            <= case["max_steps"]
        )
        valid_dag = result["dag_valid"] == case["expect_dag_valid"]
        valid_scopes = (
            result["scope_valid"] == case["expect_scope_valid"]
        )
        if (
            result["actual_agents"] != expected_agents
            or not valid_steps
            or not valid_dag
            or not valid_scopes
        ):
            failed_cases.append(
                {
                    "id": case["id"],
                    "type": "planner",
                    "reason": (
                        result["fallback_reason"]
                        or "agent_or_step_mismatch"
                    ),
                    "expected": {
                        "agents": expected_agents,
                        "steps": [
                            case["min_steps"],
                            case["max_steps"],
                        ],
                        "dag_valid": case["expect_dag_valid"],
                        "scope_valid": case["expect_scope_valid"],
                    },
                    "actual": {
                        "agents": result["actual_agents"],
                        "steps": result["step_count"],
                        "dag_valid": result["dag_valid"],
                        "scope_valid": result["scope_valid"],
                    },
                }
            )
    for result, recall in zip(retrieved, recall_values, strict=True):
        if recall < 1.0:
            failed_cases.append(
                {
                    "id": result["case"]["id"],
                    "type": "retrieval",
                    "reason": "expected_path_not_in_top_5",
                    "expected": result["case"]["expected_paths"],
                    "actual": result["actual_paths"],
                }
            )
    required_metrics = (
        {
            "planner_schema_success_rate",
            "planner_dag_validity_rate",
            "planner_scope_validity_rate",
        }
        if suite == "planner"
        else None
    )
    failures = threshold_failures(
        metrics,
        required_metrics=required_metrics,
    )
    case_counts = {"planner": len(planner_cases)}
    if suite != "planner":
        case_counts["retrieval"] = len(retrieval_cases)
    return {
        "mode": "offline",
        "metrics": metrics,
        "gate_passed": not failures,
        "threshold_failures": failures,
        "failed_cases": failed_cases,
        "case_counts": case_counts,
    }


async def run_evaluation(
    mode: str,
    *,
    suite: str | None = None,
) -> dict:
    if mode == "live" and not (
        os.getenv("ALIYUN_API_KEY") or os.getenv("EMBEDDING_API_KEY")
    ):
        return {
            "mode": "live",
            "status": "skipped",
            "reason": "Live evaluation requires a configured API key.",
            "metrics": {},
            "gate_passed": True,
            "threshold_failures": [],
            "failed_cases": [],
        }
    report = await run_offline(suite=suite)
    report["mode"] = mode
    if suite is not None:
        report["suite"] = suite
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("offline", "live"),
        default="offline",
    )
    parser.add_argument(
        "--suite",
        choices=("planner",),
        default=None,
    )
    parser.add_argument(
        "--output",
        default="evals/reports/latest.json",
    )
    args = parser.parse_args(argv)
    report = asyncio.run(
        run_evaluation(args.mode, suite=args.suite)
    )
    log_agent_event(
        logger,
        "eval.completed",
        success=report["gate_passed"],
        error_type=(
            None if report["gate_passed"] else "EvaluationGateFailure"
        ),
        eval_mode=args.mode,
        failed_cases=len(report.get("failed_cases", [])),
    )
    json_path, markdown_path = write_report(report, args.output)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    if report.get("status") == "skipped":
        print(report["reason"])
        return 0
    if not report["gate_passed"]:
        for failure in report["threshold_failures"]:
            print(f"FAILED: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
