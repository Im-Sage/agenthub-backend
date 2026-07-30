import asyncio
import json
import logging

from app.core import logging as agent_logging
from app.core.config import settings
from app.tools import audit
from app.tools.base import (
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
)
from app.tools.registry import ToolRegistry


def event_payload(caplog, event):
    for record in reversed(caplog.records):
        try:
            payload = json.loads(record.getMessage())
        except json.JSONDecodeError:
            continue
        if payload.get("event") == event:
            return payload
    raise AssertionError(f"Missing event: {event}")


def test_structured_event_redacts_secrets_paths_headers_and_content(
    caplog,
    monkeypatch,
):
    secrets = {
        "aliyun_api_key": "aliyun-super-secret",
        "github_token": "github-super-secret",
        "mcp_internal_token": "mcp-super-secret",
        "embedding_api_key": "embedding-super-secret",
    }
    for name, value in secrets.items():
        monkeypatch.setattr(settings, name, value)
    caplog.set_level(logging.INFO, logger="agenthub.observability-test")
    logger = agent_logging.get_logger("observability-test")

    agent_logging.log_agent_event(
        logger,
        "test.completed",
        task_id=11,
        conversation_id=22,
        user_id=33,
        repository_id=44,
        agent_code="backend",
        success=False,
        error_type="RuntimeError",
        error_summary=(
            "failed at C:\\private\\repository with "
            "aliyun-super-secret"
        ),
        authorization="Bearer mcp-super-secret",
        repo_path="C:\\private\\repository",
        content="complete private file contents",
    )

    rendered = caplog.text
    assert all(value not in rendered for value in secrets.values())
    assert "Bearer" not in rendered
    assert "C:\\private\\repository" not in rendered
    assert "complete private file contents" not in rendered
    payload = event_payload(caplog, "test.completed")
    assert payload["task_id"] == 11
    assert payload["success"] is False
    assert payload["error_type"] == "RuntimeError"
    assert payload["authorization"] == "<redacted>"
    assert payload["repo_path"] == "<redacted>"
    assert payload["content"] == "<redacted>"


def test_audit_argument_masking_is_recursive_and_bounded(monkeypatch):
    monkeypatch.setattr(settings, "aliyun_api_key", "hidden-key")

    masked = json.loads(
        audit._mask_arguments(
            {
                "query": "safe query",
                "headers": {"Authorization": "Bearer hidden-key"},
                "nested": {"api_key": "hidden-key"},
                "repo_path": "/srv/private/repository",
                "content": "private source code",
            }
        )
    )

    assert masked["query"] == "safe query"
    assert masked["headers"] == "<redacted>"
    assert masked["nested"]["api_key"] == "<redacted>"
    assert masked["repo_path"] == "<redacted>"
    assert masked["content"] == "<redacted>"


def test_registry_logs_low_cardinality_tool_event(
    caplog,
    monkeypatch,
):
    registry = ToolRegistry()

    async def handler(request):
        return ToolCallResult(
            success=False,
            error="C:\\private\\repo failed with secret-value",
        )

    registry.register(
        ToolDefinition(name="workspace.test", description="test"),
        handler,
    )
    monkeypatch.setattr(
        audit,
        "record_tool_call",
        lambda request, result, risk_level: None,
    )
    monkeypatch.setattr(settings, "aliyun_api_key", "secret-value")
    caplog.set_level(logging.INFO, logger="agenthub.tools")

    result = asyncio.run(
        registry.call(
            ToolCallRequest(
                name="workspace.test",
                task_id=1,
                conversation_id=2,
                user_id=3,
                repository_id=4,
            )
        )
    )

    assert result.success is False
    payload = event_payload(caplog, "mcp.tool_called")
    assert payload["tool_name"] == "workspace.test"
    assert payload["task_id"] == 1
    assert payload["repository_id"] == 4
    assert payload["success"] is False
    assert payload["error_type"] == "ToolCallError"
    assert isinstance(payload["duration_ms"], int)
    assert "secret-value" not in caplog.text
    assert "C:\\private\\repo" not in caplog.text
