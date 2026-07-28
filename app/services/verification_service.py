import json
import time
from pathlib import Path

from app.agents.graph.schemas import (
    VerificationCheck,
    VerificationResult,
)
from app.mcp.repository_resolver import RepositoryResolver
from app.core.logging import get_logger, log_agent_event
from app.services.command_runner import CommandKind, CommandRunner


_DOCUMENT_EXTENSIONS = {".md", ".txt", ".rst"}
_FRONTEND_EXTENSIONS = {
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".html",
    ".vue",
    ".svelte",
}
logger = get_logger("verification")


class VerificationService:
    def __init__(
        self,
        *,
        repository_resolver: RepositoryResolver | None = None,
        command_runner: CommandRunner | None = None,
    ):
        self.repository_resolver = (
            repository_resolver or RepositoryResolver()
        )
        self.command_runner = command_runner or CommandRunner()

    def verify(
        self,
        *,
        repository_id: int,
        user_id: int,
        changed_files: list[str],
        instruction: str,
    ) -> VerificationResult:
        started = time.perf_counter()

        def completed(result: VerificationResult) -> VerificationResult:
            last_exit_code = (
                result.checks[-1].exit_code
                if result.checks
                else None
            )
            log_agent_event(
                logger,
                "verification.completed",
                user_id=user_id,
                repository_id=repository_id,
                duration_ms=int(
                    (time.perf_counter() - started) * 1000
                ),
                success=result.success,
                error_type=(
                    None
                    if result.success
                    else "VerificationFailure"
                ),
                command_exit_code=last_exit_code,
                verification_success=result.success,
                check_count=len(result.checks),
            )
            return result

        resolved = self.repository_resolver.resolve_owned_workspace(
            repository_id,
            user_id,
        )
        workspace = Path(resolved.local_path)
        normalized_files = [
            Path(path.replace("\\", "/"))
            for path in changed_files
        ]

        if normalized_files and all(
            path.suffix.lower() in _DOCUMENT_EXTENSIONS
            for path in normalized_files
        ):
            return completed(VerificationResult(
                success=True,
                checks=[
                    VerificationCheck(
                        name="documentation_only",
                        success=True,
                        exit_code=0,
                        summary="Only documentation files changed.",
                        duration_ms=0,
                    )
                ],
            ))

        command_kinds = self._select_checks(
            workspace,
            normalized_files,
        )
        if not command_kinds:
            return completed(VerificationResult(
                success=False,
                checks=[
                    VerificationCheck(
                        name="no_applicable_checks",
                        success=False,
                        exit_code=None,
                        summary=(
                            "No applicable verification checks."
                        ),
                        duration_ms=0,
                    )
                ],
                failure_summary=(
                    "No applicable verification checks."
                ),
            ))

        checks: list[VerificationCheck] = []
        failure_lines: list[str] = []
        for command_kind in command_kinds:
            try:
                execution = self.command_runner.run(
                    workspace_path=str(workspace),
                    command_kind=command_kind,
                )
                summary = self._execution_summary(
                    execution.stdout,
                    execution.stderr,
                )
                checks.append(
                    VerificationCheck(
                        name=command_kind.value,
                        success=execution.success,
                        exit_code=execution.exit_code,
                        summary=summary,
                        duration_ms=execution.duration_ms,
                    )
                )
                if not execution.success:
                    failure_lines.append(
                        "command="
                        f"{command_kind.value} "
                        f"exit_code={execution.exit_code} "
                        f"stderr={execution.stderr[-2000:]}"
                    )
            except Exception as exc:
                error_type = type(exc).__name__
                checks.append(
                    VerificationCheck(
                        name=command_kind.value,
                        success=False,
                        exit_code=None,
                        summary=(
                            "Verification runner error: "
                            f"{error_type}"
                        ),
                        duration_ms=0,
                    )
                )
                failure_lines.append(
                    f"command={command_kind.value} "
                    f"exit_code=None error_type={error_type}"
                )

        success = all(check.success for check in checks)
        failure_summary = None
        if not success:
            failure_summary = "\n".join(failure_lines)[-4000:]
        return completed(VerificationResult(
            success=success,
            checks=checks,
            failure_summary=failure_summary,
        ))

    @classmethod
    def _select_checks(
        cls,
        workspace: Path,
        changed_files: list[Path],
    ) -> list[CommandKind]:
        python_changed = any(
            path.suffix.lower() == ".py"
            or (path.parts and path.parts[0] == "tests")
            for path in changed_files
        )
        frontend_changed = any(
            path.suffix.lower() in _FRONTEND_EXTENSIONS
            or path.name == "package.json"
            for path in changed_files
        )

        checks: list[CommandKind] = []
        if python_changed and cls._has_python_project(workspace):
            checks.append(CommandKind.PYTEST)
            if cls._has_ruff_config(workspace):
                checks.append(CommandKind.RUFF_CHECK)
            if cls._has_mypy_config(workspace):
                checks.append(CommandKind.MYPY)

        if frontend_changed and (workspace / "package.json").is_file():
            scripts = cls._package_scripts(workspace)
            package_manager = cls._package_manager(workspace)
            if "test" in scripts:
                checks.append(
                    {
                        "npm": CommandKind.NPM_TEST,
                        "pnpm": CommandKind.PNPM_TEST,
                        "yarn": CommandKind.YARN_TEST,
                    }[package_manager]
                )
            if "build" in scripts:
                checks.append(
                    {
                        "npm": CommandKind.NPM_BUILD,
                        "pnpm": CommandKind.PNPM_BUILD,
                        "yarn": CommandKind.YARN_BUILD,
                    }[package_manager]
                )
        return list(dict.fromkeys(checks))

    @staticmethod
    def _has_python_project(workspace: Path) -> bool:
        return any(
            (workspace / marker).is_file()
            for marker in (
                "pyproject.toml",
                "requirements.txt",
                "setup.py",
                "setup.cfg",
            )
        )

    @classmethod
    def _has_ruff_config(cls, workspace: Path) -> bool:
        return (
            (workspace / "ruff.toml").is_file()
            or (workspace / ".ruff.toml").is_file()
            or cls._contains_section(
                workspace / "pyproject.toml",
                "[tool.ruff]",
            )
        )

    @classmethod
    def _has_mypy_config(cls, workspace: Path) -> bool:
        return (
            (workspace / "mypy.ini").is_file()
            or (workspace / ".mypy.ini").is_file()
            or cls._contains_section(
                workspace / "pyproject.toml",
                "[tool.mypy]",
            )
            or cls._contains_section(
                workspace / "setup.cfg",
                "[mypy]",
            )
        )

    @staticmethod
    def _contains_section(path: Path, section: str) -> bool:
        if not path.is_file():
            return False
        try:
            content = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return False
        return section.lower() in content.lower()

    @staticmethod
    def _package_scripts(workspace: Path) -> dict:
        try:
            package = json.loads(
                (workspace / "package.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError):
            return {}
        scripts = package.get("scripts")
        return scripts if isinstance(scripts, dict) else {}

    @staticmethod
    def _package_manager(workspace: Path) -> str:
        if (workspace / "pnpm-lock.yaml").is_file():
            return "pnpm"
        if (workspace / "yarn.lock").is_file():
            return "yarn"
        return "npm"

    @staticmethod
    def _execution_summary(stdout: str, stderr: str) -> str:
        combined = "\n".join(
            value for value in (stdout, stderr) if value
        )
        return combined[-2000:] or "Command completed without output."


verification_service = VerificationService()
