from contextlib import asynccontextmanager
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount

from app.core.config import settings
from app.mcp.auth import InternalBearerAuthMiddleware
from app.mcp.repository_resolver import RepositoryResolver
from app.services.workspace_service import workspace_service


def _workspace_mcp_bind_config() -> tuple[str, int, str]:
    if not settings.mcp_workspace_server_url:
        return "127.0.0.1", 9000, "/mcp"

    parsed = urlparse(settings.mcp_workspace_server_url)
    return (
        parsed.hostname or "127.0.0.1",
        parsed.port or 9000,
        parsed.path or "/mcp",
    )


_host, _port, _path = _workspace_mcp_bind_config()
repository_resolver = RepositoryResolver()

mcp = FastMCP(
    "AgentHub Workspace MCP",
    host=_host,
    port=_port,
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
)


class WriteFileResult(BaseModel):
    changed_files: list[str]
    message: str


class ReadFileResult(BaseModel):
    file: str
    content: str


class ListFilesResult(BaseModel):
    files: list[str]


class SearchCodeMatch(BaseModel):
    file: str
    line: int
    text: str


class SearchCodeResult(BaseModel):
    results: list[SearchCodeMatch]


class RenameFileResult(BaseModel):
    changed_files: list[str]
    message: str


def _workspace_path(repository_id: int, user_id: int) -> str:
    return repository_resolver.resolve_owned_workspace(
        repository_id,
        user_id,
    ).local_path


@mcp.tool()
def workspace_read_file(
    repository_id: int,
    user_id: int,
    target_file: str,
) -> ReadFileResult:
    """Read a UTF-8 text file from an authorized repository workspace."""
    content = workspace_service.read_file(
        _workspace_path(repository_id, user_id),
        target_file,
    )
    return ReadFileResult(file=target_file, content=content)


@mcp.tool()
def workspace_list_files(
    repository_id: int,
    user_id: int,
    target_dir: str = ".",
    max_files: int = 200,
) -> ListFilesResult:
    """List files in an authorized repository workspace."""
    files = workspace_service.list_files(
        _workspace_path(repository_id, user_id),
        target_dir=target_dir,
        max_files=max_files,
    )
    return ListFilesResult(files=files)


@mcp.tool()
def workspace_search_code(
    repository_id: int,
    user_id: int,
    query: str,
    target_dir: str = ".",
    max_results: int = 50,
) -> SearchCodeResult:
    """Search UTF-8 files in an authorized repository workspace."""
    results = workspace_service.search_code(
        _workspace_path(repository_id, user_id),
        query=query,
        target_dir=target_dir,
        max_results=max_results,
    )
    return SearchCodeResult(
        results=[SearchCodeMatch(**item) for item in results]
    )


@mcp.tool()
async def workspace_write_file(
    repository_id: int,
    user_id: int,
    target_file: str,
    content: str,
) -> WriteFileResult:
    """Write a file in an authorized repository workspace."""
    await workspace_service.write_file(
        _workspace_path(repository_id, user_id),
        target_file,
        content,
    )
    return WriteFileResult(
        changed_files=[target_file],
        message=f"File written: {target_file}",
    )


@mcp.tool()
async def workspace_rename_file(
    repository_id: int,
    user_id: int,
    source_file: str,
    target_file: str,
) -> RenameFileResult:
    """Rename a file in an authorized repository workspace."""
    await workspace_service.rename_file(
        _workspace_path(repository_id, user_id),
        source_file,
        target_file,
    )
    return RenameFileResult(
        changed_files=[source_file, target_file],
        message=f"File renamed: {source_file} -> {target_file}",
    )


@mcp.tool()
def workspace_get_diff(
    repository_id: int,
    user_id: int,
) -> dict:
    """Return git diff for an authorized repository workspace."""
    diff = workspace_service.get_diff(
        _workspace_path(repository_id, user_id)
    )
    return {"diff": diff}


@mcp.tool()
def workspace_get_changed_files(
    repository_id: int,
    user_id: int,
) -> dict:
    """Return changed paths for an authorized repository workspace."""
    files = workspace_service.get_changed_files(
        _workspace_path(repository_id, user_id)
    )
    return {"changed_files": files}


_mcp_http_app = mcp.streamable_http_app()


@asynccontextmanager
async def _lifespan(application):
    async with mcp.session_manager.run():
        yield


_mounted_app = Starlette(
    routes=[Mount(_path, app=_mcp_http_app)],
    lifespan=_lifespan,
)


class _MissingTokenApp:
    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send(
                    {
                        "type": "lifespan.startup.failed",
                        "message": (
                            "MCP internal token must be configured "
                            "before server startup."
                        ),
                    }
                )
            return

        if scope["type"] == "http":
            response = JSONResponse(
                {"error": "MCP server is not configured"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        await _mounted_app(scope, receive, send)


app = (
    InternalBearerAuthMiddleware(
        _mounted_app,
        settings.mcp_internal_token,
    )
    if settings.mcp_internal_token
    else _MissingTokenApp()
)


if __name__ == "__main__":
    raise RuntimeError(
        "Run the MCP server with "
        "`uvicorn app.mcp.workspace_server:app`."
    )
