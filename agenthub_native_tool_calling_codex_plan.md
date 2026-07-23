# AgentHub Native Tool Calling Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace AgentHub's current “LLM emits `[FILE:]` markers → regex parser → ToolRegistry” main path with native LLM Tool Calling, while keeping the text protocol as a temporary configurable fallback.

**Architecture:** LangGraph remains responsible for workflow orchestration. `QwenAgentAdapter` becomes the single execution layer for one agent. The model receives structured tool schemas via `bind_tools`, returns structured `tool_calls`, and AgentHub converts those calls into existing `ToolCallRequest` objects. `ToolRegistry` remains the only tool governance/execution gateway and continues to own risk checks, audit logging, and Local/MCP/Hybrid routing.

**Tech Stack:** Python, LangChain `ChatOpenAI`, LangGraph, Qwen OpenAI-compatible API, existing ToolRegistry, MCP, pytest.

## Global Constraints

- Repository: `Im-Sage/agenthub-backend`.
- Preserve `ToolRegistry` as the only normal tool execution gateway.
- Preserve Local / MCP / Hybrid routing behavior.
- Do not let the model choose or override `local_path`; inject it server-side.
- Do not expose `ToolRiskLevel.HIGH` tools in this phase.
- Do not weaken existing high-risk confirmation checks.
- Do not delete the legacy text parser in this phase.
- Legacy `[FILE:]` / `[DELETE:]` / `[RENAME:]` parsing must become fallback-only.
- Keep `AgentRunRequest -> AgentAdapter.run() -> AgentRunResult` as the public execution contract.
- `plan_node` may continue to call the LLM directly.
- `execute_node` must stop implementing its own model/file-operation path and reuse an adapter.
- Use TDD and run the full suite after every task.

---

# 1. Current and Target Flow

## Current LangGraph path

```text
execute_node
  -> llm.ainvoke(messages)
  -> model emits [FILE:] / [DELETE:] / [RENAME:]
  -> apply_file_operations_with_tools()
  -> regex parser
  -> ToolCallRequest
  -> ToolRegistry
  -> Local or MCP
  -> WorkspaceService
```

## Current standalone Qwen path

```text
QwenAgentAdapter
  -> raw httpx request
  -> model emits text markers
  -> workspace_service.apply_operations_from_text()
  -> WorkspaceService
```

This path bypasses `ToolRegistry` and duplicates execution logic.

## Target flow

```text
LangGraph
  -> chooses child agent
  -> AgentAdapter.run()
  -> QwenAgentAdapter
  -> ChatOpenAI.bind_tools(...)
  -> Qwen returns AIMessage.tool_calls
  -> map model tool name to registry name
  -> ToolCallRequest
  -> ToolRegistry
      -> risk policy
      -> audit
      -> task log
      -> Local / MCP / Hybrid routing
  -> WorkspaceService
  -> ToolMessage returned to model
  -> repeat until model returns final response
```

Compatibility fallback:

```text
No tool_calls
+ response still contains legacy markers
+ AGENT_LEGACY_FILE_PROTOCOL_FALLBACK=true
  -> apply_file_operations_with_tools()
  -> ToolRegistry
```

---

# 2. Files

## Create

- `app/agents/llm_factory.py`
- `app/agents/tool_calling.py`
- `tests/test_llm_factory.py`
- `tests/test_tool_calling.py`
- `tests/test_qwen_adapter_tool_calling.py`
- `tests/test_langgraph_executor_adapter.py`

## Modify

- `app/core/config.py`
- `app/agents/qwen_adapter.py`
- `app/agents/graph/nodes.py`
- `app/tools/agent_file_ops.py`
- `app/services/workspace_service.py`
- `README.md`

## Keep unchanged unless a failing test proves otherwise

- `app/tools/registry.py`
- `app/tools/workspace_tools.py`
- `app/mcp/client.py`
- `app/agents/base.py`

---

# 3. Model-visible Tool Policy

Use these profiles:

```python
AGENT_TOOL_PROFILES = {
    "qwen": {
        "workspace.read_file",
        "workspace.list_files",
        "workspace.search_code",
        "workspace.write_file",
        "workspace.rename_file",
        "workspace.get_diff",
        "workspace.get_changed_files",
    },
    "backend": {
        "workspace.read_file",
        "workspace.list_files",
        "workspace.search_code",
        "workspace.write_file",
        "workspace.rename_file",
        "workspace.get_diff",
        "workspace.get_changed_files",
    },
    "frontend": {
        "workspace.read_file",
        "workspace.list_files",
        "workspace.search_code",
        "workspace.write_file",
        "workspace.rename_file",
        "workspace.get_diff",
        "workspace.get_changed_files",
    },
    "reviewer": {
        "workspace.read_file",
        "workspace.list_files",
        "workspace.search_code",
        "workspace.get_diff",
        "workspace.get_changed_files",
    },
}
```

Do **not** expose `workspace.delete_file` yet because it is high risk and this phase does not implement granular per-tool Human-in-the-loop approval.

The model-facing API cannot use dotted names reliably. Map:

```text
workspace.read_file  -> workspace_read_file
workspace.write_file -> workspace_write_file
```

Do not rename internal registry tools. Convert back before creating `ToolCallRequest`.

---

# Task 1: Centralize LLM Construction

**Files:**
- Create: `app/agents/llm_factory.py`
- Modify: `app/agents/graph/nodes.py`
- Test: `tests/test_llm_factory.py`

**Produces:** `get_chat_llm() -> ChatOpenAI`

- [ ] Create a failing import test for `get_chat_llm`.
- [ ] Create:

```python
from langchain_openai import ChatOpenAI

from app.core.config import settings


def get_chat_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.aliyun_model,
        openai_api_key=settings.aliyun_api_key,
        openai_api_base=settings.aliyun_base_url,
        timeout=settings.aliyun_timeout_seconds,
        temperature=0,
    )
```

- [ ] Remove the duplicate `get_llm()` implementation from `app/agents/graph/nodes.py`.
- [ ] Change `plan_node` to use `get_chat_llm()`.
- [ ] Run:

```bash
pytest tests/test_llm_factory.py -v
pytest -q
```

Expected: PASS.

---

# Task 2: Convert ToolRegistry Definitions to Model Tool Schemas

**Files:**
- Create: `app/agents/tool_calling.py`
- Test: `tests/test_tool_calling.py`

**Produces:**

```python
model_tool_name(registry_name: str) -> str
build_model_tools(
    agent_code: str,
    has_workspace: bool,
) -> tuple[list[dict], dict[str, str]]
```

Implement:

```python
from copy import deepcopy
from typing import Any

from app.tools.base import ToolDefinition, ToolRiskLevel
from app.tools.registry import tool_registry


AGENT_TOOL_PROFILES = {
    # use the exact profiles defined above
}


def model_tool_name(registry_name: str) -> str:
    return registry_name.replace(".", "_")


def _model_input_schema(definition: ToolDefinition) -> dict[str, Any]:
    schema = deepcopy(definition.input_schema or {})
    schema.setdefault("type", "object")

    properties = dict(schema.get("properties") or {})
    properties.pop("local_path", None)
    schema["properties"] = properties

    required = [
        item for item in schema.get("required", [])
        if item != "local_path"
    ]
    if required:
        schema["required"] = required
    else:
        schema.pop("required", None)

    return schema


def build_model_tools(
    agent_code: str,
    has_workspace: bool,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not has_workspace:
        return [], {}

    allowed = AGENT_TOOL_PROFILES.get(
        agent_code,
        AGENT_TOOL_PROFILES["qwen"],
    )

    tools = []
    reverse_map = {}

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
```

Required tests:

```text
workspace.write_file -> workspace_write_file
local_path is absent from model schema
local_path is absent from required
workspace.delete_file is not exposed
reviewer cannot write or rename
backend can read/write/rename
no workspace => no workspace tools
```

Run:

```bash
pytest tests/test_tool_calling.py -v
pytest -q
```

---

# HUMAN GATE 1

Stop and ask me to explain:

1. Why does the model see `workspace_write_file` but the registry uses `workspace.write_file`?
2. Why must `local_path` be hidden from the tool schema and injected by the server?
3. Why is `workspace.delete_file` intentionally not exposed?

Do not answer for me.

---

# Task 3: Implement the Native Model/Tool Loop

**Files:**
- Modify: `app/agents/tool_calling.py`
- Modify: `app/core/config.py`
- Modify: `tests/test_tool_calling.py`

Add settings:

```python
agent_tool_max_rounds: int = 8
agent_legacy_file_protocol_fallback: bool = True
```

Add result type:

```python
from dataclasses import dataclass, field
from langchain_core.messages import BaseMessage


@dataclass
class ToolCallingRunResult:
    summary: str
    changed_files: list[str] = field(default_factory=list)
    messages: list[BaseMessage] = field(default_factory=list)
    used_legacy_fallback: bool = False
```

Add:

```python
def contains_legacy_file_markers(content: str) -> bool:
    return any(
        marker in content
        for marker in ("[FILE:", "[DELETE:", "[RENAME:")
    )
```

Implement the core loop:

```python
import json

from langchain_core.messages import BaseMessage, ToolMessage

from app.core.config import settings
from app.tools.agent_file_ops import apply_file_operations_with_tools
from app.tools.base import ToolCallRequest
from app.tools.registry import tool_registry


async def run_tool_calling_agent(
    *,
    llm,
    messages: list[BaseMessage],
    agent_code: str,
    repo_path: str | None,
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

    model_tools, reverse_map = build_model_tools(
        agent_code,
        has_workspace=bool(repo_path),
    )
    model = llm.bind_tools(model_tools) if model_tools else llm

    conversation = list(messages)
    changed_files: list[str] = []

    for _ in range(round_limit):
        response = await model.ainvoke(conversation)
        conversation.append(response)

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

            return ToolCallingRunResult(
                summary=content,
                changed_files=list(dict.fromkeys(changed_files)),
                messages=conversation,
                used_legacy_fallback=False,
            )

        for call in tool_calls:
            call_id = str(call.get("id") or "")
            external_name = str(call.get("name") or "")
            registry_name = reverse_map.get(external_name)

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

            arguments = dict(call.get("args") or {})

            if registry_name.startswith("workspace."):
                if not repo_path:
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

                # Security boundary: overwrite any model-provided value.
                arguments["local_path"] = repo_path

            result = await tool_registry.call(
                ToolCallRequest(
                    name=registry_name,
                    task_id=task_id,
                    conversation_id=conversation_id,
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
```

Required unit tests with a fake LLM:

```text
1. Model emits workspace_write_file.
2. Application maps it to workspace.write_file.
3. ToolRegistry receives a ToolCallRequest.
4. A malicious model-supplied local_path is overwritten by repo_path.
5. Tool result is appended as ToolMessage.
6. Second model round receives the ToolMessage.
7. changed_files is aggregated from structured_content.
8. Unknown tool names become failed ToolMessage results, not direct execution.
9. max_rounds raises RuntimeError.
10. legacy markers execute only when fallback=True.
11. fallback=False never invokes the legacy parser.
```

Use fake responses such as:

```python
AIMessage(
    content="",
    tool_calls=[
        {
            "name": "workspace_write_file",
            "args": {
                "target_file": "app/main.py",
                "content": "print('ok')",
                "local_path": "/malicious/path",
            },
            "id": "call-1",
            "type": "tool_call",
        }
    ],
)
```

Assert the registry receives:

```python
request.name == "workspace.write_file"
request.arguments["local_path"] == "/trusted/workspace"
```

Run:

```bash
pytest tests/test_tool_calling.py -v
pytest -q
```

---

# HUMAN GATE 2

Stop and let me inspect one test flow:

```text
AIMessage.tool_calls
-> model-safe name
-> canonical registry name
-> ToolCallRequest
-> ToolRegistry
-> ToolMessage
-> second model round
```

Do not continue until I can explain this chain.

---

# Task 4: Migrate QwenAgentAdapter

**Files:**
- Modify: `app/agents/qwen_adapter.py`
- Create: `tests/test_qwen_adapter_tool_calling.py`

The adapter must no longer:

```text
use raw httpx for its main execution path
prompt the model to emit [FILE:] markers
call WorkspaceService.apply_operations_from_text()
```

Target implementation shape:

```python
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import (
    AgentAdapter,
    AgentRunRequest,
    AgentRunResult,
)
from app.agents.llm_factory import get_chat_llm
from app.agents.tool_calling import run_tool_calling_agent
from app.core.config import settings


class QwenAgentAdapter(AgentAdapter):
    async def run(
        self,
        request: AgentRunRequest,
    ) -> AgentRunResult:
        if not settings.aliyun_api_key:
            raise RuntimeError("ALIYUN_API_KEY is not configured.")

        agent_code = str(
            request.context.get("agent_code") or "qwen"
        )

        system_prompt = request.context.get(
            "system_prompt",
            "You are an AI engineer in AgentHub.",
        )

        if request.repo_path:
            system_prompt += (
                "\n\nYou have access to repository workspace tools. "
                "Use tools to inspect and modify the repository. "
                "Read relevant files before overwriting them. "
                "Do not emit custom [FILE:], [DELETE:], or [RENAME:] "
                "markers for normal workspace operations."
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=request.instruction),
        ]

        previous_error = request.context.get("previous_error")
        if previous_error:
            messages.append(
                HumanMessage(
                    content=(
                        "Previous execution failed with this error:\n"
                        f"{previous_error}\n"
                        "Inspect the current workspace and fix it."
                    )
                )
            )

        result = await run_tool_calling_agent(
            llm=get_chat_llm(),
            messages=messages,
            agent_code=agent_code,
            repo_path=request.repo_path,
            task_id=request.task_id,
            conversation_id=request.conversation_id,
        )

        return AgentRunResult(
            status="success",
            summary=result.summary,
            changed_files=result.changed_files,
            logs=(
                f"provider=aliyun "
                f"model={settings.aliyun_model} "
                f"files_changed={len(result.changed_files)} "
                f"legacy_fallback={result.used_legacy_fallback}"
            ),
        )
```

Remove obsolete imports and helpers from the current adapter:

```text
httpx
FILE_OPERATION_PROMPT
_apply_file_changes()
workspace_service.apply_operations_from_text()
```

Tests must verify:

```text
QwenAgentAdapter delegates to run_tool_calling_agent
agent_code is passed through context
repo_path is passed through
previous_error becomes additional message context
AgentRunResult.changed_files comes from ToolCallingRunResult
```

Run:

```bash
pytest tests/test_qwen_adapter_tool_calling.py -v
pytest -q
```

---

# Task 5: Make LangGraph Executor Reuse AgentAdapter

**Files:**
- Modify: `app/agents/graph/nodes.py`
- Create: `tests/test_langgraph_executor_adapter.py`

`execute_node` should own only:

```text
child task status
which agent runs
AgentRunRequest construction
storing execution result into AgentState
```

It should no longer own:

```text
workspace file-marker prompt
child-agent llm.ainvoke()
legacy parser invocation
file operation execution
```

Inside `execute_node`, after loading `agent_obj`, use:

```python
from app.agents.base import AgentRunRequest
from langchain_core.messages import AIMessage


system_prompt = (
    agent_obj.system_prompt
    or f"You are a {agent_code} engineer."
)

context = {
    "agent_code": agent_code,
    "system_prompt": system_prompt,
}

if state.get("errors"):
    context["previous_error"] = state["errors"][-1]

adapter = task_service.get_adapter(agent_obj)

result = await adapter.run(
    AgentRunRequest(
        task_id=(
            child_task.id
            if child_task
            else state["task_id"]
        ),
        conversation_id=state["conversation_id"],
        instruction=instruction,
        repo_path=repo_path,
        context=context,
        task=child_task,
    )
)

if result.status != "success":
    raise RuntimeError(
        result.summary or "Agent execution failed"
    )
```

Then return:

```python
return {
    "execution_results": [
        {
            "step": current_step_index,
            "content": result.summary,
            "files": result.changed_files,
        }
    ],
    "messages": [AIMessage(content=result.summary)],
    "errors": [],
}
```

The existing child-task DB success/failure updates must remain.

The test must monkeypatch `task_service.get_adapter()` with a fake adapter and verify:

```text
execute_node called adapter.run()
AgentRunRequest.agent context is correct
previous_error is forwarded when present
execution_results uses AgentRunResult.summary
execution_results uses AgentRunResult.changed_files
child task reaches SUCCESS
```

Run:

```bash
pytest tests/test_langgraph_executor_adapter.py -v
pytest -q
```

---

# HUMAN GATE 3

Stop and ask me to assign ownership:

```text
Who chooses the next Agent?
Who chooses which tool to call?
Who checks tool risk?
Who selects Local vs MCP?
Who performs the real filesystem write?
```

I should be able to explain:

```text
LangGraph        -> workflow orchestration
LLM              -> model-visible tool selection
ToolRegistry     -> risk governance and routing
ToolRegistry     -> Local / MCP / Hybrid choice
WorkspaceService -> filesystem safety and actual operation
```

---

# Task 6: Mark the Text Protocol as Legacy Fallback

**Files:**
- Modify: `app/tools/agent_file_ops.py`
- Modify: `app/services/workspace_service.py`
- Modify: `README.md`
- Modify: `tests/test_tool_calling.py`

Add this module docstring to `app/tools/agent_file_ops.py`:

```python
"""
Legacy compatibility parser for historical AgentHub file-operation markers.

Primary agent execution uses native LLM Tool Calling and ToolRegistry.
This parser exists only as a temporary fallback while older model responses
or saved workflows may still emit [FILE:], [DELETE:], and [RENAME:] markers.
"""
```

Keep `WorkspaceService.apply_operations_from_text()` for compatibility, but mark it deprecated in its docstring. Do not call it from `QwenAgentAdapter`.

Run repository scans:

```bash
git grep -n "When changing files, use these exact file operation markers"
git grep -n "apply_operations_from_text"
git grep -n "apply_file_operations_with_tools"
```

Expected:

```text
No active QwenAgentAdapter or LangGraph executor prompt asks for markers.
apply_operations_from_text remains only as compatibility code.
apply_file_operations_with_tools remains as the configurable fallback path.
```

Update README architecture to:

```text
LLM Native Tool Calling
-> ToolCallRequest
-> ToolRegistry
-> Local / MCP / Hybrid
-> WorkspaceService
```

State clearly that text markers are temporary fallback, not fully removed.

Run:

```bash
pytest -q
```

---

# Task 7: Manual Real-model Verification

Do not claim completion based only on mocked tests.

## Test A: Native local-tool flow

Create a disposable workspace with:

```python
# sample.py
def greet():
    return "hello"
```

Ask the backend agent:

```text
Read sample.py and change greet() so it returns "hello agenthub".
```

Expected:

```text
1. Model emits workspace_read_file tool call.
2. ToolRegistry executes workspace.read_file.
3. ToolMessage is returned to the model.
4. Model emits workspace_write_file.
5. ToolRegistry executes workspace.write_file.
6. sample.py changes.
7. Final response is normal prose, not [FILE:] markers.
8. AgentRunResult.changed_files contains sample.py.
9. Existing tool audit records contain the native calls.
```

## Test B: MCP mode

Set:

```text
MCP_ENABLED=true
MCP_TOOL_MODE=mcp
```

Repeat Test A.

Expected:

```text
same model tool call
-> same ToolCallRequest
-> same ToolRegistry
-> MCPToolClient
-> MCP workspace server
```

There must be no MCP branch inside `QwenAgentAdapter`.

## Test C: Hybrid mode

Set:

```text
MCP_ENABLED=true
MCP_TOOL_MODE=hybrid
```

Confirm existing registry routing still works.

## Test D: High-risk delete

Ask:

```text
Delete sample.py.
```

Expected in this phase:

```text
workspace_delete_file is not included in the model-bound tool list.
```

Do not weaken risk checks to make deletion work.

## Test E: Legacy fallback

Simulate a response with `[FILE:]` markers and no `tool_calls`.

With fallback enabled: parser executes through ToolRegistry.

With fallback disabled: no file operation executes.

---

# HUMAN GATE 4

I must personally inspect one real execution and record:

```text
User instruction
-> AIMessage.tool_calls
-> model-facing tool name
-> canonical ToolRegistry name
-> ToolCallRequest arguments
-> tool audit row
-> actual git diff
-> final model response
```

Codex must not do this entire verification invisibly.

---

# 4. Acceptance Criteria

## Native Tool Calling

- [ ] Qwen receives tool schemas through `bind_tools`.
- [ ] The model can return `AIMessage.tool_calls`.
- [ ] Model-facing names are safe underscore names.
- [ ] Calls map back to canonical registry names.
- [ ] `local_path` is hidden from model schemas.
- [ ] `local_path` is injected server-side.
- [ ] Tool results return through `ToolMessage`.
- [ ] Multiple tool rounds are supported.
- [ ] Tool rounds have a configured upper bound.

## Architecture

- [ ] `QwenAgentAdapter` no longer uses raw HTTP as its main agent path.
- [ ] `QwenAgentAdapter` no longer calls `apply_operations_from_text()`.
- [ ] LangGraph executor no longer prompts for file markers.
- [ ] LangGraph executor no longer parses file markers.
- [ ] LangGraph child execution goes through `AgentAdapter.run()`.
- [ ] `ToolRegistry` remains the execution gateway.
- [ ] Local/MCP/Hybrid routing remains in `ToolRegistry`.

## Safety

- [ ] High-risk tools are not exposed in this phase.
- [ ] Existing high-risk enforcement remains unchanged.
- [ ] The model cannot control the workspace root.
- [ ] `WorkspaceService.validate_path()` remains the filesystem boundary.

## Compatibility

- [ ] Legacy parsing works when fallback is enabled.
- [ ] Legacy parsing does not run when disabled.
- [ ] Existing tests pass.

## Observability

- [ ] Native tool calls create existing audit records.
- [ ] Native tool calls create existing task logs.
- [ ] `changed_files` is derived from structured tool results.
- [ ] Adapter logs record whether fallback was used.

---

# 5. Explicit Non-goals

Do not implement these in this phase:

```text
per-tool Human-in-the-loop approval
native exposure of workspace.delete_file
parallel tool calls
automatic tool retries
streaming tool-call UI
complete removal of legacy parser code
dynamic arbitrary external MCP tool discovery
shell execution tools
```

These require separate security and workflow design.

---

# 6. Next Recommended Phase

After this migration is stable:

```text
LLM requests HIGH-risk tool
-> ToolRegistry identifies HIGH risk
-> LangGraph interrupt()
-> frontend displays exact tool + arguments
-> user approves/rejects
-> Command(resume=decision)
-> approved ToolCallRequest executes
```

Only then consider exposing `workspace.delete_file` natively.

---

# 7. Prompt to Give Codex

Place this document at:

```text
docs/superpowers/plans/2026-07-22-native-tool-calling-migration.md
```

Then give Codex:

```text
Read docs/superpowers/plans/2026-07-22-native-tool-calling-migration.md in full before changing code.

Implement the plan strictly task by task.

Rules:
1. Do not execute multiple Tasks at once.
2. Before each Task, inspect the current files named in that Task and report repository drift.
3. If the repository has drifted, adapt minimally while preserving the target architecture.
4. Follow TDD: focused failing test first, then minimal production change.
5. Stop at every HUMAN GATE and wait for me.
6. Do not answer HUMAN GATE questions on my behalf.
7. Do not bypass ToolRegistry.
8. Do not expose ToolRiskLevel.HIGH tools in this phase.
9. Do not remove the legacy parser; make it fallback-only.
10. Never trust model-provided local_path; inject the workspace path server-side.
11. Do not add a second MCP execution path.
12. After every Task, run focused tests and the full test suite.
13. Show a concise git diff summary after every Task.
14. Do not commit unless I explicitly request it.
15. Do not claim completion until all acceptance criteria and manual integration tests pass.

Start with Task 1 only.
```

---

# 8. Interview-ready Architecture Description After Completion

```text
AgentHub originally used a custom text file-operation protocol such as
[FILE:], [DELETE:], and [RENAME:]. After introducing ToolRegistry and MCP,
I migrated the primary agent path to native LLM Tool Calling.

The model now receives structured tool schemas and returns structured tool_calls.
AgentHub translates the model-facing tool name into the internal canonical name
and executes it through ToolRegistry. ToolRegistry owns risk control, auditing,
task logging, and Local/MCP/Hybrid routing, while WorkspaceService remains the
final filesystem safety boundary.

The original text protocol is retained only as a configurable compatibility
fallback during migration.
```

Key sentence:

```text
LangGraph handles orchestration;
the LLM selects model-visible tools;
ToolRegistry governs and routes execution;
MCP or local handlers execute tools;
WorkspaceService enforces filesystem safety.
```
