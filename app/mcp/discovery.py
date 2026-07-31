from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

from app.core.config import Settings, settings
from app.core.logging import get_logger
from app.mcp.client import MCPToolClient
from app.tools.base import ToolDefinition, ToolRiskLevel, ToolSource
from app.tools.registry import ToolRegistry, tool_registry


logger = get_logger("mcp.discovery")
_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
_TRUSTED_PARAMETERS = {
    "local_path",
    "repository_id",
    "user_id",
    "worktree_path",
}


@dataclass(frozen=True)
class MCPDiscoveryReport:
    server_id: str
    discovered: int
    registered: int
    updated: int
    removed: int
    denied: tuple[str, ...]
    invalid: tuple[str, ...]
    error: str | None


def canonical_registry_name(
    remote_name: str,
    settings_obj: Settings = settings,
) -> str:
    if remote_name.startswith("workspace_"):
        return "workspace." + remote_name.removeprefix("workspace_")
    return f"{settings_obj.mcp_dynamic_namespace}.{remote_name}"


class MCPDiscoveryService:
    def __init__(
        self,
        *,
        registry: ToolRegistry = tool_registry,
        client: MCPToolClient | None = None,
        settings_obj: Settings = settings,
    ):
        self.registry = registry
        self.settings = settings_obj
        self.client = client

    async def refresh(self) -> MCPDiscoveryReport:
        server_id = self.settings.mcp_dynamic_server_id
        empty_report = MCPDiscoveryReport(
            server_id=server_id,
            discovered=0,
            registered=0,
            updated=0,
            removed=0,
            denied=(),
            invalid=(),
            error=None,
        )
        if (
            not self.settings.mcp_enabled
            or self.settings.mcp_tool_mode.lower() == "local"
            or not self.settings.mcp_dynamic_discovery_enabled
        ):
            return empty_report

        try:
            client = self.client or self._create_client()
            tools = await client.list_tools()
        except Exception as exc:
            if (
                self.settings.mcp_tool_mode.lower() == "mcp"
                and self.settings.mcp_dynamic_fail_closed
            ):
                raise
            logger.warning(
                "MCP discovery failed for server %s: %s",
                server_id,
                str(exc),
            )
            return MCPDiscoveryReport(
                **{
                    **empty_report.__dict__,
                    "error": str(exc),
                }
            )

        denied: list[str] = []
        invalid: list[str] = []
        definitions: dict[str, ToolDefinition] = {}
        for payload in tools:
            definition, rejection = self._to_definition(payload)
            remote_name = self._payload_name(payload)
            if rejection == "denied":
                denied.append(remote_name)
                continue
            if definition is None:
                invalid.append(remote_name)
                continue
            if definition.name in definitions:
                invalid.append(remote_name)
                continue
            definitions[definition.name] = definition

        current = {
            name: route
            for name, route in self.registry._remote_routes.items()
            if route.server_id == server_id
        }
        for name in definitions:
            route = self.registry.remote_route(name)
            if route is not None and route.server_id != server_id:
                error = (
                    f"Tool {name} is already routed by MCP server "
                    f"{route.server_id}"
                )
                if (
                    self.settings.mcp_tool_mode.lower() == "mcp"
                    and self.settings.mcp_dynamic_fail_closed
                ):
                    raise RuntimeError(error)
                return MCPDiscoveryReport(
                    server_id=server_id,
                    discovered=len(tools),
                    registered=0,
                    updated=0,
                    removed=0,
                    denied=tuple(denied),
                    invalid=tuple(invalid),
                    error=error,
                )

        registered = len(definitions.keys() - current.keys())
        removed = len(current.keys() - definitions.keys())
        updated = sum(
            current[name] != definitions[name]
            for name in current.keys() & definitions.keys()
        )

        self.registry.unregister_remote_source(server_id)
        for definition in definitions.values():
            self.registry.register_remote(definition)

        return MCPDiscoveryReport(
            server_id=server_id,
            discovered=len(tools),
            registered=registered,
            updated=updated,
            removed=removed,
            denied=tuple(denied),
            invalid=tuple(invalid),
            error=None,
        )

    def _create_client(self) -> MCPToolClient:
        if not self.settings.mcp_workspace_server_url:
            raise RuntimeError("mcp_workspace_server_url is not configured")
        return MCPToolClient(
            self.settings.mcp_workspace_server_url,
            token=self.settings.mcp_internal_token,
        )

    def _to_definition(
        self,
        payload: Any,
    ) -> tuple[ToolDefinition | None, str | None]:
        if not isinstance(payload, dict):
            return None, "invalid"
        name = self._payload_name(payload)
        description = payload.get("description")
        schema = payload.get("inputSchema")
        if (
            not _NAME_PATTERN.fullmatch(name)
            or not isinstance(description, str)
            or not description.strip()
            or not self._valid_schema(schema)
        ):
            return None, "invalid"

        denylist = self._configured_names(
            self.settings.mcp_dynamic_denylist
        )
        allowlist = self._configured_names(
            self.settings.mcp_dynamic_allowlist
        )
        if name in denylist:
            return None, "denied"
        if name not in allowlist and self._dangerous_name(name):
            return None, "denied"

        risk_level = (
            ToolRiskLevel.MEDIUM
            if name
            in self._configured_names(
                self.settings.mcp_dynamic_medium_risk_tools
            )
            else ToolRiskLevel.LOW
        )
        return (
            ToolDefinition(
                name=canonical_registry_name(name, self.settings),
                description=description.strip(),
                risk_level=risk_level,
                input_schema=self._sanitize_schema(schema),
                source=ToolSource.MCP,
                server_id=self.settings.mcp_dynamic_server_id,
                remote_name=name,
            ),
            None,
        )

    @staticmethod
    def _payload_name(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        name = payload.get("name")
        return name if isinstance(name, str) else ""

    @staticmethod
    def _configured_names(value: str) -> set[str]:
        return {
            item.strip()
            for item in (value or "").split(",")
            if item.strip()
        }

    @staticmethod
    def _dangerous_name(name: str) -> bool:
        lowered = name.lower()
        if any(
            marker in lowered
            for marker in ("delete", "remove", "shell", "exec")
        ):
            return True
        return bool(re.search(r"(^|[_.-])rm($|[_.-])", lowered))

    @staticmethod
    def _valid_schema(schema: Any) -> bool:
        if not isinstance(schema, dict) or schema.get("type") != "object":
            return False
        if not isinstance(schema.get("properties"), dict):
            return False
        required = schema.get("required")
        return required is None or isinstance(required, list)

    @staticmethod
    def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
        sanitized = deepcopy(schema)
        properties = sanitized["properties"]
        for name in _TRUSTED_PARAMETERS:
            properties.pop(name, None)
        if "required" in sanitized:
            required = [
                name
                for name in sanitized["required"]
                if name not in _TRUSTED_PARAMETERS
            ]
            if required:
                sanitized["required"] = required
            else:
                sanitized.pop("required")
        return sanitized
