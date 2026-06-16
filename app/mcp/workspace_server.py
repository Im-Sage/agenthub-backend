from urllib.parse import urlparse

from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP
from app.core.config import settings
from app.services.workspace_service import workspace_service


def _workspace_mcp_bind_config() -> tuple[str, int, str]:
    if not settings.mcp_workspace_server_url:
        return "127.0.0.1", 9000, "/mcp"

    parsed = urlparse(settings.mcp_workspace_server_url)
    return parsed.hostname or "127.0.0.1", parsed.port or 9000, parsed.path or "/mcp"


_host, _port, _path = _workspace_mcp_bind_config()

mcp = FastMCP(
    "AgentHub Workspace MCP",
    host=_host,
    port=_port,
    streamable_http_path=_path,
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


class DeleteFileResult(BaseModel):
    changed_files: list[str]
    message: str


class RenameFileResult(BaseModel):
    changed_files: list[str]
    message: str


@mcp.tool()
def workspace_read_file(local_path: str, target_file: str) -> ReadFileResult:
    """Read a UTF-8 text file inside the AgentHub repository workspace after path safety validation."""
    content = workspace_service.read_file(local_path, target_file)
    return ReadFileResult(file=target_file, content=content)


@mcp.tool()
def workspace_list_files(local_path: str, target_dir: str = ".", max_files: int = 200) -> ListFilesResult:
    """List files inside the AgentHub repository workspace, skipping dependency and generated directories."""
    files = workspace_service.list_files(local_path, target_dir=target_dir, max_files=max_files)
    return ListFilesResult(files=files)


@mcp.tool()
def workspace_search_code(
    local_path: str,
    query: str,
    target_dir: str = ".",
    max_results: int = 50,
) -> SearchCodeResult:
    """Search UTF-8 text files inside the AgentHub repository workspace."""
    results = workspace_service.search_code(
        local_path,
        query=query,
        target_dir=target_dir,
        max_results=max_results,
    )
    return SearchCodeResult(results=[SearchCodeMatch(**item) for item in results])


@mcp.tool()
async def workspace_write_file(local_path: str, target_file: str, content: str) -> WriteFileResult:
    """Write a file inside the AgentHub repository workspace after path safety validation."""
    await workspace_service.write_file(local_path, target_file, content)
    return WriteFileResult(changed_files=[target_file], message=f"File written: {target_file}")


@mcp.tool()
async def workspace_rename_file(local_path: str, source_file: str, target_file: str) -> RenameFileResult:
    """Rename a file inside the AgentHub repository workspace after path safety validation."""
    await workspace_service.rename_file(local_path, source_file, target_file)
    return RenameFileResult(
        changed_files=[source_file, target_file],
        message=f"File renamed: {source_file} -> {target_file}",
    )


@mcp.tool()
async def workspace_delete_file(local_path: str, target_file: str) -> DeleteFileResult:
    """Delete a file inside the AgentHub repository workspace after path safety validation."""
    await workspace_service.delete_file(local_path, target_file)
    return DeleteFileResult(changed_files=[target_file], message=f"File deleted: {target_file}")


@mcp.tool()
def workspace_get_diff(local_path: str) -> dict:
    """Return git diff for the current AgentHub workspace."""
    diff = workspace_service.get_diff(local_path)
    return {"diff": diff}


@mcp.tool()
def workspace_get_changed_files(local_path: str) -> dict:
    """Return changed file paths for the current AgentHub workspace."""
    files = workspace_service.get_changed_files(local_path)
    return {"changed_files": files}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
