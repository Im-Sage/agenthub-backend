from types import SimpleNamespace

import pytest

import app.tools.bootstrap as bootstrap_module
from app.tools.bootstrap import (
    initialize_tool_registry,
    initialize_tool_registry_sync,
    register_local_tools_once,
)


def test_register_local_tools_once_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bootstrap_module,
        "_local_tools_registered",
        False,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "register_builtin_tools",
        lambda: calls.append("registered"),
    )

    register_local_tools_once()
    register_local_tools_once()

    assert calls == ["registered"]


@pytest.mark.anyio
async def test_initialize_registers_local_before_dynamic_discovery(monkeypatch):
    events = []
    report = SimpleNamespace(server_id="workspace")

    class FakeDiscoveryService:
        async def refresh(self):
            events.append("discovery")
            return report

    monkeypatch.setattr(
        bootstrap_module,
        "_local_tools_registered",
        False,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "register_builtin_tools",
        lambda: events.append("local"),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "MCPDiscoveryService",
        FakeDiscoveryService,
    )
    monkeypatch.setattr(bootstrap_module.settings, "mcp_enabled", True)
    monkeypatch.setattr(
        bootstrap_module.settings,
        "mcp_dynamic_discovery_enabled",
        True,
    )
    monkeypatch.setattr(
        bootstrap_module.settings,
        "mcp_tool_mode",
        "hybrid",
    )

    result = await initialize_tool_registry()

    assert result is report
    assert events == ["local", "discovery"]


@pytest.mark.anyio
async def test_initialize_local_mode_does_not_construct_discovery(monkeypatch):
    calls = []

    class ForbiddenDiscoveryService:
        def __init__(self):
            raise AssertionError("local mode must not construct discovery")

    monkeypatch.setattr(
        bootstrap_module,
        "_local_tools_registered",
        False,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "register_builtin_tools",
        lambda: calls.append("local"),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "MCPDiscoveryService",
        ForbiddenDiscoveryService,
    )
    monkeypatch.setattr(bootstrap_module.settings, "mcp_enabled", False)

    result = await initialize_tool_registry()

    assert result is None
    assert calls == ["local"]


def test_sync_initializer_returns_async_report(monkeypatch):
    report = SimpleNamespace(server_id="workspace")

    async def fake_initialize():
        return report

    monkeypatch.setattr(
        bootstrap_module,
        "initialize_tool_registry",
        fake_initialize,
    )

    assert initialize_tool_registry_sync() is report


def test_celery_worker_process_initializer_bootstraps_tools(monkeypatch):
    from app.workers import celery_app as celery_module

    calls = []
    monkeypatch.setattr(
        celery_module,
        "initialize_tool_registry_sync",
        lambda: calls.append("initialized"),
    )

    celery_module.initialize_worker_tools()

    assert calls == ["initialized"]


@pytest.mark.anyio
async def test_fastapi_lifespan_initializes_tools_before_subscription(
    monkeypatch,
):
    from app import main as main_module

    events = []

    async def initialize():
        events.append("tools")

    async def subscribe(pattern, callback):
        events.append(("subscribe", pattern, callback))

    async def stop():
        events.append("stop")

    monkeypatch.setattr(
        main_module,
        "initialize_tool_registry",
        initialize,
    )
    monkeypatch.setattr(main_module.broadcaster, "subscribe", subscribe)
    monkeypatch.setattr(main_module.broadcaster, "stop", stop)

    async with main_module.lifespan(main_module.app):
        events.append("yield")

    assert events == [
        "tools",
        (
            "subscribe",
            "conv_*",
            main_module.websocket_manager.broadcast_json,
        ),
        "yield",
        "stop",
    ]
