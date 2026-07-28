import hmac
from app.core.logging import get_logger, log_agent_event

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


logger = get_logger("mcp.auth")


class InternalBearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str):
        configured_token = token.strip()
        if not configured_token:
            raise RuntimeError(
                "MCP internal token must be configured before server startup."
            )
        self.app = app
        self.token = configured_token

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        authorization = ""
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                authorization = value.decode("latin-1")
                break

        prefix = "Bearer "
        provided_token = (
            authorization[len(prefix):]
            if authorization.startswith(prefix)
            else ""
        )
        authorized = bool(provided_token) and hmac.compare_digest(
            provided_token,
            self.token,
        )
        if not authorized:
            log_agent_event(
                logger,
                "mcp.auth_rejected",
                success=False,
                error_type="Unauthorized",
            )
            response = JSONResponse(
                {"error": "Unauthorized"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
