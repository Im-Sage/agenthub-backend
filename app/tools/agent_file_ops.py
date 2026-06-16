import re
from app.tools.base import ToolCallRequest
from app.tools.registry import tool_registry


async def apply_file_operations_with_tools(
    local_path: str,
    content: str,
    task_id: int | None = None,
    conversation_id: int | None = None,
) -> list[str]:
    changed_files: list[str] = []

    for match in re.finditer(r"\[RENAME:\s*(.+?)\s*->\s*(.+?)\s*\]", content):
        source_file = match.group(1).strip()
        target_file = match.group(2).strip()
        result = await tool_registry.call(ToolCallRequest(
            name="workspace.rename_file",
            task_id=task_id,
            conversation_id=conversation_id,
            arguments={
                "local_path": local_path,
                "source_file": source_file,
                "target_file": target_file,
            },
        ))
        if result.success:
            changed_files.extend([source_file, target_file])
        else:
            raise RuntimeError(result.error or "rename_file failed")

    for match in re.finditer(r"\[DELETE:\s*(.+?)\s*\]", content):
        target_file = match.group(1).strip()
        result = await tool_registry.call(ToolCallRequest(
            name="workspace.delete_file",
            task_id=task_id,
            conversation_id=conversation_id,
            require_confirmation=False, # 第一阶段先绕过确认，之后集成到前端
            arguments={"local_path": local_path, "target_file": target_file},
        ))
        if result.success:
            changed_files.append(target_file)
        else:
            raise RuntimeError(result.error or "delete_file failed")

    file_pattern = r"\[FILE:\s*(.+?)\]\s*\n\s*```.*?\n([\s\S]*?)\n```"
    for match in re.finditer(file_pattern, content):
        file_path = match.group(1).strip()
        file_content = match.group(2)
        result = await tool_registry.call(ToolCallRequest(
            name="workspace.write_file",
            task_id=task_id,
            conversation_id=conversation_id,
            arguments={
                "local_path": local_path,
                "target_file": file_path,
                "content": file_content,
            },
        ))
        if result.success:
            changed_files.append(file_path)
        else:
            raise RuntimeError(result.error or "write_file failed")

    return list(dict.fromkeys(changed_files))
