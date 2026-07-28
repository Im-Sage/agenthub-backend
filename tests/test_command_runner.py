import subprocess

import pytest

from app.core.config import Settings
from app.services.command_runner import (
    CommandKind,
    CommandRunner,
    CommandValidationError,
)


def test_command_runner_settings_have_safe_defaults():
    assert (
        Settings.model_fields[
            "agent_command_timeout_seconds"
        ].default
        == 120
    )
    assert (
        Settings.model_fields[
            "agent_command_max_output_chars"
        ].default
        == 50_000
    )
    assert "ALIYUN_API_KEY" not in (
        Settings.model_fields[
            "agent_command_allowed_env"
        ].default
    )


def test_command_kind_rejects_arbitrary_shell_commands():
    with pytest.raises(ValueError):
        CommandKind("shell")


@pytest.mark.parametrize(
    "target",
    [
        "../outside",
        "tests/../../outside",
        "C:/absolute/path",
        "C:\\absolute\\path",
        "/absolute/path",
        "tests; rm -rf workspace",
        "tests && whoami",
        "tests | type secrets",
    ],
)
def test_command_runner_rejects_unsafe_targets(tmp_path, target):
    runner = CommandRunner()

    with pytest.raises(CommandValidationError):
        runner.run(
            workspace_path=str(tmp_path),
            command_kind=CommandKind.PYTEST,
            target=target,
        )


class FakeProcess:
    pid = 1234
    returncode = 0

    def communicate(self, timeout=None):
        return "stdout-content", "stderr-content"


def test_command_runner_uses_fixed_argv_cwd_and_filtered_environment(
    monkeypatch,
    tmp_path,
):
    from app.services import command_runner as command_runner_module

    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setenv("PATH", "C:\\safe-bin")
    monkeypatch.setenv("PYTHONPATH", ".")
    monkeypatch.setenv("ALIYUN_API_KEY", "aliyun-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("SECRET_KEY", "application-secret")
    monkeypatch.setenv("MCP_INTERNAL_TOKEN", "mcp-secret")
    monkeypatch.setattr(command_runner_module.shutil, "which", lambda *args, **kwargs: "pytest")
    monkeypatch.setattr(command_runner_module.subprocess, "Popen", fake_popen)

    result = CommandRunner(
        allowed_env="PATH,PYTHONPATH",
    ).run(
        workspace_path=str(tmp_path),
        command_kind=CommandKind.PYTEST,
        target="tests/unit",
    )

    assert captured["argv"] == ["pytest", "-q", "tests/unit"]
    assert captured["shell"] is False
    assert captured["cwd"] == str(tmp_path.resolve())
    assert captured["env"] == {
        "PATH": "C:\\safe-bin",
        "PYTHONPATH": ".",
    }
    assert result.success is True
    assert result.exit_code == 0


def test_command_runner_truncates_stdout_and_stderr(
    monkeypatch,
    tmp_path,
):
    from app.services import command_runner as command_runner_module

    monkeypatch.setattr(command_runner_module.shutil, "which", lambda *args, **kwargs: "pytest")
    monkeypatch.setattr(
        command_runner_module.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(),
    )

    result = CommandRunner(max_output_chars=6).run(
        workspace_path=str(tmp_path),
        command_kind=CommandKind.PYTEST,
    )

    assert result.stdout == "stdout"
    assert result.stderr == "stderr"
    assert result.truncated is True


class TimeoutProcess:
    pid = 4321
    returncode = None

    def __init__(self):
        self.calls = 0

    def communicate(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired(["pytest"], timeout)
        self.returncode = -9
        return "partial output", "timed out"


def test_command_runner_terminates_process_tree_after_timeout(
    monkeypatch,
    tmp_path,
):
    from app.services import command_runner as command_runner_module

    process = TimeoutProcess()
    terminated = []
    monkeypatch.setattr(command_runner_module.shutil, "which", lambda *args, **kwargs: "pytest")
    monkeypatch.setattr(
        command_runner_module.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        CommandRunner,
        "_terminate_process_tree",
        lambda self, actual_process: terminated.append(actual_process.pid),
    )

    result = CommandRunner(timeout_seconds=1).run(
        workspace_path=str(tmp_path),
        command_kind=CommandKind.PYTEST,
    )

    assert terminated == [4321]
    assert result.timed_out is True
    assert result.success is False


def test_missing_executable_returns_structured_failure(
    monkeypatch,
    tmp_path,
):
    from app.services import command_runner as command_runner_module

    monkeypatch.setattr(
        command_runner_module.shutil,
        "which",
        lambda *args, **kwargs: None,
    )

    result = CommandRunner().run(
        workspace_path=str(tmp_path),
        command_kind=CommandKind.PYTEST,
    )

    assert result.success is False
    assert result.exit_code is None
    assert "not available" in result.stderr


@pytest.mark.parametrize(
    "command_kind",
    [CommandKind.RUFF_CHECK, CommandKind.MYPY],
)
def test_generic_pyproject_does_not_enable_unconfigured_quality_tools(
    monkeypatch,
    tmp_path,
    command_kind,
):
    from app.services import command_runner as command_runner_module

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        command_runner_module.shutil,
        "which",
        lambda *args, **kwargs: "tool",
    )
    monkeypatch.setattr(
        command_runner_module.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail(
            "unconfigured quality tool must not execute"
        ),
    )

    result = CommandRunner().run(
        workspace_path=str(tmp_path),
        command_kind=command_kind,
    )

    assert result.success is False
    assert "configuration is not available" in result.stderr


def test_command_tools_expose_only_optional_target():
    from app.agents.tool_calling import AGENT_TOOL_PROFILES
    from app.tools.command_tools import register_command_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_command_tools(registry)
    definitions = {
        definition.name: definition
        for definition in registry.list_tools()
    }

    assert set(definitions) == {
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.run_type_check",
        "workspace.run_build",
    }
    for definition in definitions.values():
        assert set(
            definition.input_schema["properties"]
        ) == {"target"}
        assert "required" not in definition.input_schema

    assert {
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.run_type_check",
    } <= AGENT_TOOL_PROFILES["backend"]
    assert {
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.run_build",
    } <= AGENT_TOOL_PROFILES["frontend"]
    assert {
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.run_type_check",
        "workspace.run_build",
    } <= AGENT_TOOL_PROFILES["reviewer"]


@pytest.mark.anyio
async def test_run_tests_tool_uses_resolved_workspace_and_auditable_result(
    monkeypatch,
    tmp_path,
):
    from app.mcp.repository_resolver import ResolvedWorkspace
    from app.services.command_runner import CommandExecutionResult
    from app.tools import command_tools
    from app.tools.base import ToolCallRequest

    (tmp_path / "requirements.txt").write_text(
        "pytest\n",
        encoding="utf-8",
    )
    calls = []

    class FakeResolver:
        def resolve_owned_workspace(self, repository_id, user_id):
            assert (repository_id, user_id) == (42, 7)
            return ResolvedWorkspace(42, 7, str(tmp_path))

    class FakeRunner:
        def run(self, **kwargs):
            calls.append(kwargs)
            return CommandExecutionResult(
                command_kind=CommandKind.PYTEST,
                argv=["pytest", "-q", "tests/unit"],
                exit_code=0,
                stdout="2 passed",
                stderr="",
                duration_ms=25,
                timed_out=False,
                truncated=False,
                success=True,
            )

    monkeypatch.setattr(
        command_tools,
        "repository_resolver",
        FakeResolver(),
    )
    monkeypatch.setattr(command_tools, "command_runner", FakeRunner())

    result = await command_tools.workspace_run_tests(
        ToolCallRequest(
            name="workspace.run_tests",
            arguments={"target": "tests/unit"},
            repository_id=42,
            user_id=7,
        )
    )

    assert calls == [
        {
            "workspace_path": str(tmp_path),
            "command_kind": CommandKind.PYTEST,
            "target": "tests/unit",
        }
    ]
    assert result.success is True
    assert result.structured_content == {
        "command_kind": "pytest",
        "target": "tests/unit",
        "exit_code": 0,
        "duration_ms": 25,
        "timed_out": False,
        "truncated": False,
        "success": True,
    }
