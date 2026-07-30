from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage

from app.core.config import settings
from app.tools.agent_file_ops import apply_file_operations_with_tools
from app.tools.base import (
    ToolCallRequest,
    ToolDefinition,
    ToolRiskLevel,
)
from app.tools.registry import tool_registry

# AGENT_TOOL_PROFILES 定义了不同类型的智能体（agent）可以使用的工具集合。
# 每个智能体类型（如 "qwen"、"backend"、"frontend"、"reviewer"）都有一组允许调用的工具名称。
# 这些工具名称对应于在系统中注册的工具，智能体只能调用其配置文件中列出的工具，从而限制了它们的操作范围和权限。
AGENT_TOOL_PROFILES = {
    "qwen": {
        "workspace.read_file",
        "workspace.list_files",
        "workspace.search_code",
        "workspace.semantic_search",
        "workspace.write_file",
        "workspace.rename_file",
        "workspace.get_diff",
        "workspace.get_changed_files",
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.run_type_check",
        "workspace.run_build",
    },
    "backend": {
        "workspace.read_file",
        "workspace.list_files",
        "workspace.search_code",
        "workspace.semantic_search",
        "workspace.write_file",
        "workspace.rename_file",
        "workspace.get_diff",
        "workspace.get_changed_files",
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.run_type_check",
    },
    "frontend": {
        "workspace.read_file",
        "workspace.list_files",
        "workspace.search_code",
        "workspace.semantic_search",
        "workspace.write_file",
        "workspace.rename_file",
        "workspace.get_diff",
        "workspace.get_changed_files",
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.run_build",
    },
    "reviewer": {
        "workspace.read_file",
        "workspace.list_files",
        "workspace.search_code",
        "workspace.semantic_search",
        "workspace.get_diff",
        "workspace.get_changed_files",
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.run_type_check",
        "workspace.run_build",
    },
}


@dataclass
class ToolCallingRunResult:
    summary: str
    changed_files: list[str] = field(default_factory=list)
    messages: list[BaseMessage] = field(default_factory=list)
    used_legacy_fallback: bool = False


def model_tool_name(registry_name: str) -> str:
    return registry_name.replace(".", "_")


def contains_legacy_file_markers(content: str) -> bool:
    return any(
        marker in content
        for marker in ("[FILE:", "[DELETE:", "[RENAME:")
    )


def _model_input_schema(
        definition: ToolDefinition,
) -> dict[str, Any]:
    schema = deepcopy(definition.input_schema or {})
    schema.setdefault("type", "object")

    properties = dict(schema.get("properties") or {})

    # 这些参数来自服务端可信调用上下文，不能由模型查看或覆盖。
    for trusted_parameter in (
        "local_path",
        "repository_id",
        "user_id",
    ):
        properties.pop(trusted_parameter, None)
    schema["properties"] = properties

    required = [
        item
        for item in schema.get("required", [])
        if item not in {"local_path", "repository_id", "user_id"}
    ]

    if required:
        schema["required"] = required
    else:
        schema.pop("required", None)

    return schema


"""
build_model_tools 函数用于根据 Agent 角色筛选工具。
参数说明：
- agent_code: 智能体代码，用于确定允许调用的工具集合。
- has_workspace: 布尔值，表示是否关联了可信的工作区。如果为 False，则不允许调用任何工作区相关的工具。
返回值：
- tuple[list[dict[str, Any]], dict[str, str]]: 返回一个元组，包含两个元素：
  1. 工具列表（list[dict[str, Any]]），每个工具包含类型、名称、描述和参数等信息，供模型调用。
  2. 工具名称映射（dict[str, str]），将模型工具名称映射到注册表中的工具名称，用于在工具调用时进行名称转换。
"""
def build_model_tools(
        agent_code: str,
        has_workspace: bool, # 是否关联了可信的工作区
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not has_workspace:
        return [], {}

    allowed = AGENT_TOOL_PROFILES.get(
        agent_code,
        AGENT_TOOL_PROFILES["qwen"],
    )

    tools: list[dict[str, Any]] = []
    reverse_map: dict[str, str] = {}

    for definition in tool_registry.list_tools():
        if definition.name not in allowed:
            continue

        if definition.risk_level == ToolRiskLevel.HIGH:
            continue

        external_name = model_tool_name(definition.name)

        if (
                external_name in reverse_map
                and reverse_map[external_name] != definition.name
        ):
            raise RuntimeError(
                f"Tool name collision for {external_name}"
            )

        reverse_map[external_name] = definition.name

        tools.append(
            {
                "type": "function",
                "function": {
                    "name": external_name,
                    "description": definition.description,
                    "parameters": _model_input_schema(definition),
                },
            }
        )

    return tools, reverse_map


"""
run_tool_calling_agent 函数是一个异步函数，用于执行智能体的工具调用过程。
它接收语言模型（llm）、对话消息、智能体代码、仓库路径、任务ID、会话ID等参数，
并根据配置和工具定义，循环与模型交互，处理工具调用请求，直到达到最大轮数或没有更多工具调用为止。
最终返回一个 ToolCallingRunResult 对象，包含对话摘要、变更文件列表、对话消息以及是否使用了旧的文件操作回退机制。
参数说明：
- llm: 语言模型对象，用于生成响应。
- messages: 对话消息列表，包含用户输入和模型响应。
- agent_code: 智能体代码，用于确定允许调用的工具集合。
- repo_path: 仓库路径，如果为空，则不允许访问工作区工具。
- task_id: 任务ID，用于跟踪工具调用的上下文。
- conversation_id: 会话ID，用于跟踪工具调用的上下文。
- legacy_fallback: 是否使用旧的文件操作回退机制，如果为 None，则使用全局配置。
- max_rounds: 最大轮数限制，如果为 None，则使用全局配置。
返回值：
- ToolCallingRunResult 对象，包含对话摘要、变更文件列表、对话消息以及是否使用了旧的文件操作回退机制。
"""
async def run_tool_calling_agent(
    *,
    llm,
    messages: list[BaseMessage],
    agent_code: str,
    repo_path: str | None,
    repository_id: int | None = None,
    user_id: int | None = None,
    task_id: int | None,
    conversation_id: int | None,
    legacy_fallback: bool | None = None,
    max_rounds: int | None = None,
) -> ToolCallingRunResult:
    use_fallback = (
        settings.agent_legacy_file_protocol_fallback
        if legacy_fallback is None
        else legacy_fallback
    )
    round_limit = max_rounds or settings.agent_tool_max_rounds

    # build_model_tools 函数根据智能体代码和工作区关联状态，构建模型可调用的工具列表和工具名称映射。
    model_tools, reverse_map = build_model_tools(
        agent_code,
        has_workspace=bool(
            repo_path
            or (repository_id is not None and user_id is not None)
        ),
    )
    # bind_tools 方法将工具绑定到语言模型（llm）上，使得模型可以调用这些工具。
    # 它会将工具的定义传递给模型，使模型在生成响应时能够识别和调用这些工具。
    model = llm.bind_tools(model_tools) if model_tools else llm

    # conversation 列表用于存储与模型的对话消息，包括用户输入和模型的响应。它会随着每轮交互而更新，记录整个对话过程。
    conversation = list(messages)
    changed_files: list[str] = []


    for _ in range(round_limit):
        # ainvoke 方法是一个异步调用，用于向模型发送当前的对话消息（conversation），并获取模型的响应。
        # 这里的模型响应可能包含工具调用请求（tool_calls），也可能只是普通的文本响应。
        # response是模型建议调用的工具列表，每个工具调用包含工具名称、参数等信息。模型可能会在响应中建议调用一个或多个工具，以便执行特定的操作。
        response = await model.ainvoke(conversation)
        conversation.append(response)

        # getattr(response, "tool_calls", []) or [] 获取模型响应中的工具调用请求（tool_calls）。
        # 如果模型响应中没有工具调用请求，则返回一个空列表。这样可以确保在后续处理中，即使没有工具调用请求，也不会引发错误。
        # tool_calls是模型 建议 调用的工具列表，每个工具调用包含工具名称、参数等信息。模型可能会在响应中建议调用一个或多个工具，以便执行特定的操作。
        tool_calls = list(
            getattr(response, "tool_calls", []) or []
        )

        if not tool_calls:
            content = str(response.content or "")

            if (
                use_fallback
                and repo_path
                and contains_legacy_file_markers(content)
            ):
                fallback_files = (
                    await apply_file_operations_with_tools(
                        local_path=repo_path,
                        content=content,
                        task_id=task_id,
                        conversation_id=conversation_id,
                    )
                )
                changed_files.extend(fallback_files)
                return ToolCallingRunResult(
                    summary=content,
                    changed_files=list(dict.fromkeys(changed_files)),
                    messages=conversation,
                    used_legacy_fallback=True,
                )
            # 如果模型响应中没有工具调用请求，并且不需要使用旧的文件操作回退机制，则直接返回一个 ToolCallingRunResult 对象，
            # 其中包含对话摘要、变更文件列表、对话消息以及是否使用了旧的文件操作回退机制（此处为 False）。
            return ToolCallingRunResult(
                summary=content,
                changed_files=list(dict.fromkeys(changed_files)),
                messages=conversation,
                used_legacy_fallback=False,
            )
        # tool_calls 列表中包含了所有模型建议调用的工具，每个工具调用包含工具名称、参数等信息。
        for call in tool_calls:
            call_id = str(call.get("id") or "") # 工具调用的唯一标识
            external_name = str(call.get("name") or "")
            registry_name = reverse_map.get(external_name)
            # 如果模型请求调用的工具名称在 reverse_map 中找不到对应的注册表名称，则说明该工具是未知的或不允许调用的。
            if registry_name is None:
                conversation.append(
                    ToolMessage(
                        content=json.dumps(
                            {
                                "success": False,
                                "error": (
                                    f"Unknown model tool: {external_name}"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        tool_call_id=call_id,
                    )
                )
                continue
            # arguments 字典用于存储工具调用的参数，从模型请求中获取 "args" 字段，如果没有提供参数，则使用空字典。
            arguments = dict(call.get("args") or {})
            for trusted_parameter in (
                "local_path",
                "repository_id",
                "user_id",
            ):
                arguments.pop(trusted_parameter, None)
            # 如果工具名称以 "workspace." 开头，表示这是一个工作区相关的工具调用。在这种情况下，如果没有提供 repo_path，则无法访问工作区，因此会返回一个错误消息，提示工作区不可用。
            if registry_name.startswith("workspace."):
                if not (
                    (repository_id is not None and user_id is not None)
                    or repo_path
                ):
                    conversation.append(
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "success": False,
                                    "error": "Workspace is unavailable.",
                                },
                                ensure_ascii=False,
                            ),
                            tool_call_id=call_id,
                        )
                    )
                    continue

                if (
                    repository_id is None
                    and user_id is None
                    and repo_path
                    and not settings.mcp_enabled
                ):
                    arguments["local_path"] = repo_path

            # 这里调用 tool_registry.call 方法来执行工具调用请求。
            # 它会将工具名称、任务ID、会话ID和参数传递给工具注册表，以便实际执行工具的逻辑。
            result = await tool_registry.call(
                ToolCallRequest(
                    name=registry_name,
                    task_id=task_id,
                    conversation_id=conversation_id,
                    repository_id=repository_id,
                    user_id=user_id,
                    arguments=arguments,
                    require_confirmation=False,
                )
            )

            if result.success:
                files = result.structured_content.get(
                    "changed_files",
                    [],
                )
                if isinstance(files, list):
                    changed_files.extend(str(path) for path in files)
            # 每一轮工具调用的结果都会被封装成一个 ToolMessage 对象，并附加到 conversation 列表中，以便在后续的对话中可以参考这些结果。
            conversation.append(
                ToolMessage(
                    content=json.dumps(
                        {
                            "success": result.success,
                            "content": result.content,
                            "structured_content": (
                                result.structured_content
                            ),
                            "error": result.error,
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=call_id,
                )
            )

    raise RuntimeError(
        f"Agent exceeded maximum tool-calling rounds: {round_limit}"
    )
