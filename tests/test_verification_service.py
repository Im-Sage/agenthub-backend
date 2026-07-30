import asyncio

import pytest

from app.agents.graph.schemas import (
    VerificationCheck,
    VerificationResult,
)
from app.agents.graph import nodes
from app.agents.base import AgentRunResult
from app.mcp.repository_resolver import ResolvedWorkspace
from app.services.command_runner import (
    CommandExecutionResult,
    CommandKind,
)
from app.services.verification_service import VerificationService
from tests.test_langgraph_executor_adapter import (
    _executor_state,
    _install_executor_fakes,
)


class FakeResolver:
    def __init__(self, workspace):
        self.workspace = workspace
        self.calls = []

    def resolve_owned_workspace(self, repository_id, user_id):
        self.calls.append((repository_id, user_id))
        return ResolvedWorkspace(
            repository_id,
            user_id,
            str(self.workspace),
        )


class FakeRunner:
    def __init__(self, failures=None):
        self.failures = set(failures or [])
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        command_kind = kwargs["command_kind"]
        success = command_kind not in self.failures
        return CommandExecutionResult(
            command_kind=command_kind,
            argv=[command_kind.value],
            exit_code=0 if success else 1,
            stdout="checks passed" if success else "",
            stderr="" if success else f"{command_kind.value} failed",
            duration_ms=15,
            timed_out=False,
            truncated=False,
            success=success,
        )


def verify(workspace, changed_files, failures=None):
    runner = FakeRunner(failures)
    service = VerificationService(
        repository_resolver=FakeResolver(workspace),
        command_runner=runner,
    )
    result = service.verify(
        repository_id=42,
        user_id=7,
        changed_files=changed_files,
        instruction="Implement and verify the change",
    )
    return result, runner


def test_python_change_selects_pytest(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "pytest\n",
        encoding="utf-8",
    )

    result, runner = verify(tmp_path, ["app/service.py"])

    assert result.success is True
    assert [call["command_kind"] for call in runner.calls] == [
        CommandKind.PYTEST
    ]


def test_frontend_change_selects_package_tests_and_build(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run","build":"vite build"}}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("", encoding="utf-8")

    result, runner = verify(tmp_path, ["src/App.tsx"])

    assert result.success is True
    assert [call["command_kind"] for call in runner.calls] == [
        CommandKind.NPM_TEST,
        CommandKind.NPM_BUILD,
    ]


def test_mixed_backend_and_frontend_runs_both_check_families(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "pytest\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"vite build"}}',
        encoding="utf-8",
    )

    result, runner = verify(
        tmp_path,
        ["app/api.py", "src/App.tsx"],
    )

    assert result.success is True
    assert [call["command_kind"] for call in runner.calls] == [
        CommandKind.PYTEST,
        CommandKind.NPM_BUILD,
    ]


def test_documentation_only_change_skips_expensive_commands(tmp_path):
    result, runner = verify(
        tmp_path,
        ["README.md", "docs/security.md"],
    )

    assert result.success is True
    assert runner.calls == []
    assert result.checks == [
        VerificationCheck(
            name="documentation_only",
            success=True,
            exit_code=0,
            summary="Only documentation files changed.",
            duration_ms=0,
        )
    ]


def test_no_applicable_configuration_is_not_reported_as_success(tmp_path):
    result, runner = verify(tmp_path, ["src/main.go"])

    assert result.success is False
    assert runner.calls == []
    assert result.checks[0].name == "no_applicable_checks"
    assert result.failure_summary == "No applicable verification checks."


def test_failed_command_produces_bounded_structured_failure_summary(
    tmp_path,
):
    (tmp_path / "requirements.txt").write_text(
        "pytest\n",
        encoding="utf-8",
    )

    result, runner = verify(
        tmp_path,
        ["app/service.py"],
        failures={CommandKind.PYTEST},
    )

    assert result.success is False
    assert result.checks[0].exit_code == 1
    assert "pytest" in result.failure_summary
    assert "exit_code=1" in result.failure_summary
    assert "pytest failed" in result.failure_summary
    assert len(result.failure_summary) <= 4_000


class FailingVerificationService:
    def verify(self, **kwargs):
        return VerificationResult(
            success=False,
            checks=[
                VerificationCheck(
                    name="pytest",
                    success=False,
                    exit_code=1,
                    summary="tests/test_api.py failed",
                    duration_ms=25,
                )
            ],
            failure_summary=(
                "command=pytest exit_code=1 "
                "stderr=tests/test_api.py failed"
            ),
        )


def verification_state(attempts=0):
    return {
        "plan": [
            {
                "agent": "backend",
                "instruction": "Implement the API",
            }
        ],
        "current_step_index": 0,
        "current_instruction": "Implement the API",
        "execution_results": [
            {
                "step": 0,
                "content": "Implemented",
                "files": ["app/api.py"],
            }
        ],
        "repository_id": 42,
        "user_id": 7,
        "errors": [],
        "verification_attempts": attempts,
    }


def test_verify_node_records_real_failure_for_next_repair(monkeypatch):
    monkeypatch.setattr(
        nodes,
        "verification_service",
        FailingVerificationService(),
    )

    result = asyncio.run(nodes.verify_node(verification_state()))

    assert result["verification_attempts"] == 1
    assert result["errors"] == [
        "command=pytest exit_code=1 "
        "stderr=tests/test_api.py failed"
    ]
    assert result["verification_results"][0]["success"] is False
    assert result["is_finished"] is False


@pytest.mark.anyio
async def test_verification_failure_becomes_executor_repair_context(
    monkeypatch,
):
    monkeypatch.setattr(
        nodes,
        "verification_service",
        FailingVerificationService(),
    )
    verification = await nodes.verify_node(verification_state())
    observed = _install_executor_fakes(
        monkeypatch,
        AgentRunResult(
            status="success",
            summary="Repaired the failing test.",
            changed_files=["app/api.py"],
        ),
    )

    await nodes.execute_node(
        _executor_state(
            observed.child_task.id,
            errors=verification["errors"],
        )
    )

    previous_error = observed.adapter_requests[0].context["previous_error"]
    assert "command=pytest" in previous_error
    assert "exit_code=1" in previous_error
    assert "stderr=tests/test_api.py failed" in previous_error


def test_verify_node_stops_after_two_automatic_repairs(monkeypatch):
    monkeypatch.setattr(
        nodes,
        "verification_service",
        FailingVerificationService(),
    )

    result = asyncio.run(nodes.verify_node(verification_state(attempts=2)))

    assert result["verification_attempts"] == 3
    assert result["is_finished"] is True
