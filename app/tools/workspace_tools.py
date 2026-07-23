from app.tools.base import ToolCallRequest, ToolCallResult, ToolDefinition, ToolRiskLevel
from app.tools.registry import tool_registry
from app.services.workspace_service import workspace_service, WorkspaceError
from app.db.session import SessionLocal
from app.models.task import Task


async def workspace_write_file(request: ToolCallRequest) -> ToolCallResult:
    local_path = request.arguments.get("local_path")
    target_file = request.arguments.get("target_file")
    content = request.arguments.get("content", "")

    if not local_path or not target_file:
        return ToolCallResult(success=False, error="local_path and target_file are required")

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
    local_path = request.arguments.get("local_path")
    source_file = request.arguments.get("source_file")
    target_file = request.arguments.get("target_file")

    if not local_path or not source_file or not target_file:
        return ToolCallResult(success=False, error="local_path, source_file, and target_file are required")

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
    local_path = request.arguments.get("local_path")
    target_file = request.arguments.get("target_file")

    if not local_path or not target_file:
        return ToolCallResult(success=False, error="local_path and target_file are required")

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
    local_path = request.arguments.get("local_path")
    target_file = request.arguments.get("target_file")

    if not local_path or not target_file:
        return ToolCallResult(success=False, error="local_path and target_file are required")

    try:
        content = workspace_service.read_file(local_path, target_file)
        return ToolCallResult(
            success=True,
            content=content,
            structured_content={"file": target_file, "content": content},
        )
    except WorkspaceError as exc:
        return ToolCallResult(success=False, error=str(exc))


async def workspace_list_files(request: ToolCallRequest) -> ToolCallResult:
    local_path = request.arguments.get("local_path")
    target_dir = request.arguments.get("target_dir", ".")
    max_files = request.arguments.get("max_files", 200)

    if not local_path:
        return ToolCallResult(success=False, error="local_path is required")

    try:
        files = workspace_service.list_files(local_path, target_dir=target_dir, max_files=max_files)
        return ToolCallResult(
            success=True,
            content="Files listed successfully",
            structured_content={"files": files},
        )
    except WorkspaceError as exc:
        return ToolCallResult(success=False, error=str(exc))


async def workspace_search_code(request: ToolCallRequest) -> ToolCallResult:
    local_path = request.arguments.get("local_path")
    query = request.arguments.get("query")
    target_dir = request.arguments.get("target_dir", ".")
    max_results = request.arguments.get("max_results", 50)

    if not local_path or not query:
        return ToolCallResult(success=False, error="local_path and query are required")

    try:
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
    except WorkspaceError as exc:
        return ToolCallResult(success=False, error=str(exc))


async def workspace_get_diff(request: ToolCallRequest) -> ToolCallResult:
    local_path = request.arguments.get("local_path")

    if not local_path:
        return ToolCallResult(success=False, error="local_path is required")

    try:
        diff = workspace_service.get_diff(local_path)
        return ToolCallResult(
            success=True,
            content="Diff retrieved successfully",
            structured_content={"diff": diff},
        )
    except WorkspaceError as exc:
        return ToolCallResult(success=False, error=str(exc))


async def workspace_get_changed_files(request: ToolCallRequest) -> ToolCallResult:
    local_path = request.arguments.get("local_path")

    if not local_path:
        return ToolCallResult(success=False, error="local_path is required")

    try:
        files = workspace_service.get_changed_files(local_path)
        return ToolCallResult(
            success=True,
            content="Changed files retrieved successfully",
            structured_content={"changed_files": files},
        )
    except WorkspaceError as exc:
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
