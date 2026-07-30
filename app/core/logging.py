import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
EVENT_FIELDS = (
    "task_id",
    "conversation_id",
    "user_id",
    "repository_id",
    "agent_code",
    "tool_name",
    "duration_ms",
    "success",
    "error_type",
    "context_tokens",
    "retrieval_chunks",
    "command_exit_code",
    "verification_success",
)
_REDACTED_KEYS = {
    "authorization",
    "headers",
    "content",
    "file_content",
    "prompt",
    "messages",
    "local_path",
    "repo_path",
    "repository_path",
    "workspace_path",
}
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:[\\/][^\s,;\"']+")
_UNIX_PATH = re.compile(
    r"(?<![:\w])/(?:[\w.@+-]+/)+[\w.@+-]+"
)
_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


def configure_logging() -> None:
    level_name = getattr(settings, "log_level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=level, format=LOG_FORMAT)
    else:
        root_logger.setLevel(level)

    logging.getLogger("agenthub").setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"agenthub.{name}")


def _is_secret_key(key: str) -> bool:
    normalized = key.casefold()
    return (
        normalized in _REDACTED_KEYS
        or normalized in {"token", "password", "secret", "api_key"}
        or normalized.endswith(("_token", "_password", "_secret", "_api_key"))
    )


def _configured_secrets() -> list[str]:
    values = [
        settings.aliyun_api_key,
        settings.github_token,
        settings.mcp_internal_token,
        settings.embedding_api_key,
    ]
    return [
        value
        for value in values
        if isinstance(value, str) and value
    ]


def sanitize_event_value(key: str, value: Any) -> Any:
    if _is_secret_key(key):
        return "<redacted>"
    if isinstance(value, BaseException):
        return {"error_type": type(value).__name__}
    if isinstance(value, dict):
        return {
            str(nested_key): sanitize_event_value(
                str(nested_key),
                nested_value,
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [
            sanitize_event_value(key, nested_value)
            for nested_value in value
        ]
    if isinstance(value, str):
        sanitized = value
        for secret in _configured_secrets():
            sanitized = sanitized.replace(secret, "<redacted>")
        sanitized = _BEARER.sub("<redacted>", sanitized)
        sanitized = _WINDOWS_PATH.sub("<redacted-path>", sanitized)
        sanitized = _UNIX_PATH.sub("<redacted-path>", sanitized)
        return sanitized[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


def safe_error_summary(value: str | None) -> str | None:
    if value is None:
        return None
    return sanitize_event_value("error_summary", value)[:240]


def log_agent_event(
    logger: logging.Logger,
    event: str,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {
        "event": event,
        **{field: None for field in EVENT_FIELDS},
    }
    for key, value in fields.items():
        payload[key] = sanitize_event_value(key, value)
    logger.info(
        "%s",
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.logger = get_logger("request")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.logger.exception(
                "request_failed method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        self.logger.info(
            "request_completed method=%s path=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
