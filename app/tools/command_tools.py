from pathlib import Path

from app.mcp.repository_resolver import (
    RepositoryResolver,
    WorkspaceAuthorizationError,
)
from app.services.command_runner import (
    CommandExecutionResult,
    CommandKind,
    CommandRunner,
    CommandValidationError,
)
from app.tools.base import (
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
    ToolRiskLevel,
)
from app.tools.registry import ToolRegistry, tool_registry


repository_resolver = RepositoryResolver()
command_runner = CommandRunner()


def _is_python_workspace(workspace: Path) -> bool:
    return any(
        (workspace / marker).is_file()
        for marker in (
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "setup.cfg",
        )
    )


def _package_command(
    workspace: Path,
    *,
    script: str,
) -> CommandKind | None:
    if not (workspace / "package.json").is_file():
        return None
    if (workspace / "pnpm-lock.yaml").is_file():
        return (
            CommandKind.PNPM_TEST
            if script == "test"
            else CommandKind.PNPM_BUILD
        )
    if (workspace / "yarn.lock").is_file():
        return (
            CommandKind.YARN_TEST
            if script == "test"
            else CommandKind.YARN_BUILD
        )
    return (
        CommandKind.NPM_TEST
        if script == "test"
        else CommandKind.NPM_BUILD
    )


def _resolve(request: ToolCallRequest) -> Path:
    if request.repository_id is None or request.user_id is None:
        raise WorkspaceAuthorizationError(
            "Trusted repository_id and user_id are required"
        )
    resolved = repository_resolver.resolve_owned_workspace(
        request.repository_id,
        request.user_id,
    )
    return Path(resolved.local_path)


def _result(
    execution: CommandExecutionResult,
    target: str | None,
) -> ToolCallResult:
    details = {
        "command_kind": execution.command_kind.value,
        "target": target,
        "exit_code": execution.exit_code,
        "duration_ms": execution.duration_ms,
        "timed_out": execution.timed_out,
        "truncated": execution.truncated,
        "success": execution.success,
    }
    content = execution.stdout
    if execution.stderr:
        content = (
            f"{content}\n{execution.stderr}".strip()
            if content
            else execution.stderr
        )
    return ToolCallResult(
        success=execution.success,
        content=content,
        structured_content=details,
        error=None if execution.success else execution.stderr or "Command failed",
    )


def _not_applicable(message: str) -> ToolCallResult:
    return ToolCallResult(success=False, error=message)


def _run(
    request: ToolCallRequest,
    command_kind: CommandKind,
) -> ToolCallResult:
    target = request.arguments.get("target")
    try:
        workspace = _resolve(request)
        execution = command_runner.run(
            workspace_path=str(workspace),
            command_kind=command_kind,
            target=target,
        )
    except (WorkspaceAuthorizationError, CommandValidationError) as exc:
        return ToolCallResult(success=False, error=str(exc))
    return _result(execution, target)


async def workspace_run_tests(
    request: ToolCallRequest,
) -> ToolCallResult:
    try:
        workspace = _resolve(request)
    except WorkspaceAuthorizationError as exc:
        return ToolCallResult(success=False, error=str(exc))
    command_kind = (
        CommandKind.PYTEST
        if _is_python_workspace(workspace)
        else _package_command(workspace, script="test")
    )
    if command_kind is None:
        return _not_applicable(
            "No supported test configuration is available"
        )
    return _run(request, command_kind)


async def workspace_run_lint(
    request: ToolCallRequest,
) -> ToolCallResult:
    try:
        workspace = _resolve(request)
    except WorkspaceAuthorizationError as exc:
        return ToolCallResult(success=False, error=str(exc))
    if not _is_python_workspace(workspace):
        return _not_applicable(
            "No supported lint configuration is available"
        )
    return _run(request, CommandKind.RUFF_CHECK)


async def workspace_run_type_check(
    request: ToolCallRequest,
) -> ToolCallResult:
    try:
        workspace = _resolve(request)
    except WorkspaceAuthorizationError as exc:
        return ToolCallResult(success=False, error=str(exc))
    if not _is_python_workspace(workspace):
        return _not_applicable(
            "No supported type-check configuration is available"
        )
    return _run(request, CommandKind.MYPY)


async def workspace_run_build(
    request: ToolCallRequest,
) -> ToolCallResult:
    try:
        workspace = _resolve(request)
    except WorkspaceAuthorizationError as exc:
        return ToolCallResult(success=False, error=str(exc))
    command_kind = _package_command(workspace, script="build")
    if command_kind is None:
        return _not_applicable(
            "No supported build configuration is available"
        )
    return _run(request, command_kind)


def register_command_tools(
    registry: ToolRegistry = tool_registry,
) -> None:
    schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": (
                    "Optional repository-relative test or source target"
                ),
            }
        },
    }
    tools = (
        (
            "workspace.run_tests",
            "Run the repository's allowlisted test command.",
            workspace_run_tests,
        ),
        (
            "workspace.run_lint",
            "Run the repository's allowlisted lint command.",
            workspace_run_lint,
        ),
        (
            "workspace.run_type_check",
            "Run the repository's allowlisted type-check command.",
            workspace_run_type_check,
        ),
        (
            "workspace.run_build",
            "Run the repository's allowlisted build command.",
            workspace_run_build,
        ),
    )
    for name, description, handler in tools:
        registry.register(
            ToolDefinition(
                name=name,
                description=description,
                risk_level=ToolRiskLevel.MEDIUM,
                input_schema=schema,
            ),
            handler,
        )
