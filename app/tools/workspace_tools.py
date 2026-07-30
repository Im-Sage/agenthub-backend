import logging

from app.core.config import settings
from app.mcp.repository_resolver import (
    RepositoryResolver,
    WorkspaceAuthorizationError,
)
from app.tools.base import ToolCallRequest, ToolCallResult, ToolDefinition, ToolRiskLevel
from app.tools.registry import tool_registry
from app.services.workspace_service import workspace_service, WorkspaceError
from app.db.session import SessionLocal
from app.models.task import Task


logger = logging.getLogger(__name__)
repository_resolver = RepositoryResolver()


def _resolve_workspace_path(request: ToolCallRequest) -> str:
    if request.repository_id is not None and request.user_id is not None:
        return repository_resolver.resolve_owned_workspace(
            request.repository_id,
            request.user_id,
        ).local_path

    local_path = request.arguments.get("local_path")
    if not settings.mcp_enabled and local_path:
        logger.warning(
            "Using legacy internal local_path fallback for tool=%s",
            request.name,
        )
        return str(local_path)

    raise WorkspaceAuthorizationError(
        "Trusted repository_id and user_id are required"
    )


async def workspace_write_file(request: ToolCallRequest) -> ToolCallResult:
    target_file = request.arguments.get("target_file")
    content = request.arguments.get("content", "")

    if not target_file:
        return ToolCallResult(success=False, error="target_file is required")
    try:
        local_path = _resolve_workspace_path(request)
    except WorkspaceAuthorizationError as exc:
        return ToolCallResult(success=False, error=str(exc))

    db = SessionLocal()
    try:
        task = db.get(Task, request.task_id) if request.task_id else None
        await workspace_service.write_file(local_path, target_file, content, task=task)
        return ToolCallResult(
            success=True,
            content=f"File written: {target_file}",
            structured_content={"changed_files": [target_file]},
        )
    except WorkspaceError as exc:
        return ToolCallResult(success=False, error=str(exc))
    finally:
        db.close()


async def workspace_rename_file(request: ToolCallRequest) -> ToolCallResult:
    source_file = request.arguments.get("source_file")
    target_file = request.arguments.get("target_file")

    if not source_file or not target_file:
        return ToolCallResult(success=False, error="source_file and target_file are required")
    try:
        local_path = _resolve_workspace_path(request)
    except WorkspaceAuthorizationError as exc:
        return ToolCallResult(success=False, error=str(exc))

    db = SessionLocal()
    try:
        task = db.get(Task, request.task_id) if request.task_id else None
        await workspace_service.rename_file(local_path, source_file, target_file, task=task)
        return ToolCallResult(
            success=True,
            content=f"File renamed: {source_file} -> {target_file}",
            structured_content={"changed_files": [source_file, target_file]},
        )
    except WorkspaceError as exc:
        return ToolCallResult(success=False, error=str(exc))
    finally:
        db.close()


async def workspace_delete_file(request: ToolCallRequest) -> ToolCallResult:
    target_file = request.arguments.get("target_file")

    if not target_file:
        return ToolCallResult(success=False, error="target_file is required")
    try:
        local_path = _resolve_workspace_path(request)
    except WorkspaceAuthorizationError as exc:
        return ToolCallResult(success=False, error=str(exc))

    db = SessionLocal()
    try:
        task = db.get(Task, request.task_id) if request.task_id else None
        await workspace_service.delete_file(local_path, target_file, task=task)
        return ToolCallResult(
            success=True,
            content=f"File deleted: {target_file}",
            structured_content={"changed_files": [target_file]},
        )
    except WorkspaceError as exc:
        return ToolCallResult(success=False, error=str(exc))
    finally:
        db.close()


"""
workspace_read_file 函数用于读取当前仓库工作区中的 UTF-8 文本文件。
它接受一个 ToolCallRequest 对象作为参数，该对象包含以下字段：
- local_path: 当前仓库的本地路径。
- target_file: 要读取的目标文件的相对路径。
函数返回一个 ToolCallResult 对象，表示读取操作的结果。该对象包含以下字段：
- success: 布尔值，表示操作是否成功。
- content: 字符串，表示读取的文件内容。
- structured_content: 字典，包含文件路径和内容的结构化信息。
如果读取操作失败，函数将返回一个包含错误信息的 ToolCallResult 对象。 
"""
async def workspace_read_file(request: ToolCallRequest) -> ToolCallResult:
    target_file = request.arguments.get("target_file")

    if not target_file:
        return ToolCallResult(success=False, error="target_file is required")

    try:
        local_path = _resolve_workspace_path(request)
        content = workspace_service.read_file(local_path, target_file)
        return ToolCallResult(
            success=True,
            content=content,
            structured_content={"file": target_file, "content": content},
        )
    except (WorkspaceAuthorizationError, WorkspaceError) as exc:
        return ToolCallResult(success=False, error=str(exc))


async def workspace_list_files(request: ToolCallRequest) -> ToolCallResult:
    target_dir = request.arguments.get("target_dir", ".")
    max_files = request.arguments.get("max_files", 200)

    try:
        local_path = _resolve_workspace_path(request)
        files = workspace_service.list_files(local_path, target_dir=target_dir, max_files=max_files)
        return ToolCallResult(
            success=True,
            content="Files listed successfully",
            structured_content={"files": files},
        )
    except (WorkspaceAuthorizationError, WorkspaceError) as exc:
        return ToolCallResult(success=False, error=str(exc))


async def workspace_search_code(request: ToolCallRequest) -> ToolCallResult:
    query = request.arguments.get("query")
    target_dir = request.arguments.get("target_dir", ".")
    max_results = request.arguments.get("max_results", 50)

    if not query:
        return ToolCallResult(success=False, error="query is required")

    try:
        local_path = _resolve_workspace_path(request)
        results = workspace_service.search_code(
            local_path,
            query=query,
            target_dir=target_dir,
            max_results=max_results,
        )
        return ToolCallResult(
            success=True,
            content="Search completed successfully",
            structured_content={"results": results},
        )
    except (WorkspaceAuthorizationError, WorkspaceError) as exc:
        return ToolCallResult(success=False, error=str(exc))


async def workspace_get_diff(request: ToolCallRequest) -> ToolCallResult:
    try:
        local_path = _resolve_workspace_path(request)
        diff = workspace_service.get_diff(local_path)
        return ToolCallResult(
            success=True,
            content="Diff retrieved successfully",
            structured_content={"diff": diff},
        )
    except (WorkspaceAuthorizationError, WorkspaceError) as exc:
        return ToolCallResult(success=False, error=str(exc))


async def workspace_get_changed_files(request: ToolCallRequest) -> ToolCallResult:
    try:
        local_path = _resolve_workspace_path(request)
        files = workspace_service.get_changed_files(local_path)
        return ToolCallResult(
            success=True,
            content="Changed files retrieved successfully",
            structured_content={"changed_files": files},
        )
    except (WorkspaceAuthorizationError, WorkspaceError) as exc:
        return ToolCallResult(success=False, error=str(exc))


def register_workspace_tools() -> None:
    tool_registry.register(
        ToolDefinition(
            name="workspace.read_file",
            description="Read a UTF-8 text file from the current repository workspace.",
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "properties": {
                    "local_path": {"type": "string"},
                    "target_file": {"type": "string"},
                },
                "required": ["local_path", "target_file"],
            },
        ),
        workspace_read_file,
    )

    tool_registry.register(
        ToolDefinition(
            name="workspace.list_files",
            description="List files inside the current repository workspace, skipping generated and dependency directories.",
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "properties": {
                    "local_path": {"type": "string"},
                    "target_dir": {"type": "string", "default": "."},
                    "max_files": {"type": "integer", "default": 200},
                },
                "required": ["local_path"],
            },
        ),
        workspace_list_files,
    )

    tool_registry.register(
        ToolDefinition(
            name="workspace.search_code",
            description="Search UTF-8 text files inside the current repository workspace.",
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "properties": {
                    "local_path": {"type": "string"},
                    "query": {"type": "string"},
                    "target_dir": {"type": "string", "default": "."},
                    "max_results": {"type": "integer", "default": 50},
                },
                "required": ["local_path", "query"],
            },
        ),
        workspace_search_code,
    )

    tool_registry.register(
        ToolDefinition(
            name="workspace.write_file",
            description="Write text content to a relative file inside the current repository workspace.",
            risk_level=ToolRiskLevel.MEDIUM,
            input_schema={
                "type": "object",
                "properties": {
                    "local_path": {"type": "string"},
                    "target_file": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["local_path", "target_file", "content"],
            },
        ),
        workspace_write_file, # 回调函数
    )
    
    tool_registry.register(
        ToolDefinition(
            name="workspace.rename_file",
            description="Rename a file inside the current repository workspace.",
            risk_level=ToolRiskLevel.MEDIUM,
            input_schema={
                "type": "object",
                "properties": {
                    "local_path": {"type": "string"},
                    "source_file": {"type": "string"},
                    "target_file": {"type": "string"},
                },
                "required": ["local_path", "source_file", "target_file"],
            },
        ),
        workspace_rename_file,
    )

    tool_registry.register(
        ToolDefinition(
            name="workspace.delete_file",
            description="Delete a file inside the current repository workspace.",
            risk_level=ToolRiskLevel.HIGH,
            input_schema={
                "type": "object",
                "properties": {
                    "local_path": {"type": "string"},
                    "target_file": {"type": "string"},
                },
                "required": ["local_path", "target_file"],
            },
        ),
        workspace_delete_file,
    )

    tool_registry.register(
        ToolDefinition(
            name="workspace.get_diff",
            description="Get the git diff for the current repository workspace.",
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "properties": {
                    "local_path": {"type": "string"},
                },
                "required": ["local_path"],
            },
        ),
        workspace_get_diff,
    )

    tool_registry.register(
        ToolDefinition(
            name="workspace.get_changed_files",
            description="Get the list of changed files for the current repository workspace.",
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "properties": {
                    "local_path": {"type": "string"},
                },
                "required": ["local_path"],
            },
        ),
        workspace_get_changed_files,
    )
