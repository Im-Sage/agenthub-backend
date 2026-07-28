import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from enum import Enum

from pydantic import BaseModel

from app.core.config import settings


class CommandKind(str, Enum):
    PYTEST = "pytest"
    RUFF_CHECK = "ruff_check"
    MYPY = "mypy"
    NPM_TEST = "npm_test"
    NPM_BUILD = "npm_build"
    PNPM_TEST = "pnpm_test"
    PNPM_BUILD = "pnpm_build"
    YARN_TEST = "yarn_test"
    YARN_BUILD = "yarn_build"


class CommandValidationError(ValueError):
    pass


class CommandExecutionResult(BaseModel):
    command_kind: CommandKind
    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    truncated: bool
    success: bool


_COMMANDS: dict[CommandKind, list[str]] = {
    CommandKind.PYTEST: ["pytest", "-q"],
    CommandKind.RUFF_CHECK: ["ruff", "check", "."],
    CommandKind.MYPY: ["mypy", "app"],
    CommandKind.NPM_TEST: ["npm", "test", "--", "--runInBand"],
    CommandKind.NPM_BUILD: ["npm", "run", "build"],
    CommandKind.PNPM_TEST: ["pnpm", "test"],
    CommandKind.PNPM_BUILD: ["pnpm", "run", "build"],
    CommandKind.YARN_TEST: ["yarn", "test"],
    CommandKind.YARN_BUILD: ["yarn", "build"],
}


class CommandRunner:
    def __init__(
        self,
        *,
        timeout_seconds: int | float | None = None,
        max_output_chars: int | None = None,
        allowed_env: str | None = None,
    ):
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.agent_command_timeout_seconds
        )
        self.max_output_chars = (
            max_output_chars
            if max_output_chars is not None
            else settings.agent_command_max_output_chars
        )
        allowed = (
            allowed_env
            if allowed_env is not None
            else settings.agent_command_allowed_env
        )
        self.allowed_env = {
            name.strip().upper()
            for name in allowed.split(",")
            if name.strip()
        }

    def run(
        self,
        *,
        workspace_path: str,
        command_kind: CommandKind,
        target: str | None = None,
    ) -> CommandExecutionResult:
        started = time.perf_counter()
        workspace = Path(workspace_path).resolve()
        if not workspace.is_dir():
            raise CommandValidationError(
                "workspace_path must be an existing directory"
            )

        safe_target = self._validate_target(target)
        argv = self._build_argv(command_kind, safe_target)
        environment = self._filtered_environment()

        unavailable_reason = self._unavailable_reason(
            workspace,
            command_kind,
            environment,
        )
        if unavailable_reason:
            return self._failure(
                command_kind,
                argv,
                unavailable_reason,
                started,
            )

        popen_kwargs = {
            "shell": False,
            "cwd": str(workspace),
            "env": environment,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(argv, **popen_kwargs)
        except FileNotFoundError:
            return self._failure(
                command_kind,
                argv,
                f"Executable is not available: {argv[0]}",
                started,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Unable to start restricted command: {type(exc).__name__}"
            ) from exc

        timed_out = False
        try:
            stdout, stderr = process.communicate(
                timeout=self.timeout_seconds
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_tree(process)
            stdout, stderr = process.communicate()

        stdout, stdout_truncated = self._truncate(stdout or "")
        stderr, stderr_truncated = self._truncate(stderr or "")
        exit_code = process.returncode
        duration_ms = int((time.perf_counter() - started) * 1000)
        return CommandExecutionResult(
            command_kind=command_kind,
            argv=argv,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            truncated=stdout_truncated or stderr_truncated,
            success=not timed_out and exit_code == 0,
        )

    @staticmethod
    def _validate_target(target: str | None) -> str | None:
        if target is None:
            return None
        candidate = target.strip()
        if not candidate:
            return None
        if (
            "\x00" in candidate
            or "\n" in candidate
            or "\r" in candidate
            or any(character in candidate for character in ";&|`")
            or "$(" in candidate
        ):
            raise CommandValidationError(
                "target contains forbidden shell characters"
            )
        normalized = candidate.replace("\\", "/")
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or ".." in normalized.split("/")
        ):
            raise CommandValidationError(
                "target must be a repository-relative path"
            )
        return normalized

    @staticmethod
    def _build_argv(
        command_kind: CommandKind,
        target: str | None,
    ) -> list[str]:
        argv = list(_COMMANDS[command_kind])
        if not target:
            return argv
        if command_kind == CommandKind.PYTEST:
            argv.append(target)
        elif command_kind == CommandKind.RUFF_CHECK:
            argv[-1] = target
        elif command_kind == CommandKind.MYPY:
            argv[-1] = target
        return argv

    def _filtered_environment(self) -> dict[str, str]:
        return {
            key: value
            for key, value in os.environ.items()
            if key.upper() in self.allowed_env
        }

    @staticmethod
    def _unavailable_reason(
        workspace: Path,
        command_kind: CommandKind,
        environment: dict[str, str],
    ) -> str | None:
        executable = _COMMANDS[command_kind][0]
        if shutil.which(
            executable,
            path=environment.get("PATH", ""),
        ) is None:
            return f"Executable is not available: {executable}"

        if command_kind == CommandKind.RUFF_CHECK:
            if not (
                (workspace / "ruff.toml").is_file()
                or (workspace / ".ruff.toml").is_file()
                or CommandRunner._contains_section(
                    workspace / "pyproject.toml",
                    "[tool.ruff]",
                )
            ):
                return "Ruff configuration is not available"
        elif command_kind == CommandKind.MYPY:
            if not (
                (workspace / "mypy.ini").is_file()
                or (workspace / ".mypy.ini").is_file()
                or CommandRunner._contains_section(
                    workspace / "pyproject.toml",
                    "[tool.mypy]",
                )
                or CommandRunner._contains_section(
                    workspace / "setup.cfg",
                    "[mypy]",
                )
            ):
                return "Mypy configuration is not available"
        elif command_kind in {
            CommandKind.NPM_TEST,
            CommandKind.NPM_BUILD,
            CommandKind.PNPM_TEST,
            CommandKind.PNPM_BUILD,
            CommandKind.YARN_TEST,
            CommandKind.YARN_BUILD,
        }:
            package_file = workspace / "package.json"
            if not package_file.is_file():
                return "package.json is not available"
            try:
                package = json.loads(
                    package_file.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                return "package.json is invalid"
            script = (
                "test"
                if command_kind
                in {
                    CommandKind.NPM_TEST,
                    CommandKind.PNPM_TEST,
                    CommandKind.YARN_TEST,
                }
                else "build"
            )
            if script not in (package.get("scripts") or {}):
                return f"package.json script is not available: {script}"
        return None

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

    def _truncate(self, value: str) -> tuple[str, bool]:
        if len(value) <= self.max_output_chars:
            return value, False
        return value[: self.max_output_chars], True

    def _failure(
        self,
        command_kind: CommandKind,
        argv: list[str],
        message: str,
        started: float,
    ) -> CommandExecutionResult:
        return CommandExecutionResult(
            command_kind=command_kind,
            argv=argv,
            exit_code=None,
            stdout="",
            stderr=message,
            duration_ms=int((time.perf_counter() - started) * 1000),
            timed_out=False,
            truncated=False,
            success=False,
        )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        if os.name == "nt":
            try:
                subprocess.run(
                    [
                        "taskkill",
                        "/F",
                        "/T",
                        "/PID",
                        str(process.pid),
                    ],
                    shell=False,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                process.kill()
            return

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
