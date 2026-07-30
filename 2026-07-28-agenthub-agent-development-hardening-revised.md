# AgentHub Agent 开发实习导向完善实施计划

> **给 Codex 的执行指令：**完整阅读本文档后，从 Task 0 开始按顺序执行所有任务。使用测试驱动开发；每个 Task 完成后运行该 Task 的定向测试，并在阶段结束时运行全量测试。本文中的 **HUMAN GATE** 仅指“把实现工作留给用户补充”的执行中断：不得向用户索要方案选择、代码片段、配置内容、文档段落或测试结论；不得留下 TODO、TBD、占位实现、空白章节、伪代码替代实现或“请用户后续完成”的步骤。Codex 必须依据仓库现有架构自主作出合理工程决策，完成代码、迁移、测试、文档和验证。该约束与 AgentHub 运行时的 LangGraph HITL、`approval_node`、Interrupt/Resume、CodeChange 审核确认等产品逻辑无关，现有 HITL 逻辑必须保留。除非缺少真实外部密钥导致 live 集成测试客观无法运行，否则持续修复并执行到全部验收项通过；缺少密钥时仍须完成 Fake/Mock 测试和全部本地实现，不得转交用户补做。

**Goal：** 将 AgentHub 从“能够通过 Tool Calling 修改代码的 Agent Demo”完善为具备可靠规划、安全 MCP 边界、自动测试验证、代码语义检索、统一上下文装配和可量化评测能力的 Coding Agent 系统，用于 Agent 开发日常实习项目展示。

**Architecture：** 保留现有 FastAPI、Celery、Redis、LangGraph、ToolRegistry、WorkspaceService 和 QwenAgentAdapter 主架构。新增的能力必须通过边界清晰的服务接入：Planner 只负责结构化规划；ToolRegistry 负责授权、路由与审计；MCP Server 只接受仓库身份而不是本地路径；CommandRunner 只执行枚举化白名单命令；Code RAG 提供仓库级混合检索；ContextAssembler 统一装配模型上下文；Evaluation Runner 对关键链路进行离线评测。

**Tech Stack：** Python 3.11+、FastAPI、SQLAlchemy 2.x、Alembic、Celery、Redis、LangGraph、LangChain、Pydantic v2、MCP Python SDK v1、httpx、GitPython、pytest。

## Global Constraints

- 本文中的 HUMAN GATE 是 Codex 实施过程概念，不是 AgentHub 产品中的 HITL 概念。
- Codex 不得把任何实现、选择、补写、测试或排错工作留给用户；必须自行读取仓库、选择与现有模式兼容的方案并完成。
- 不得在提交代码中留下 `TODO`、`TBD`、`pass` 占位、`NotImplementedError` 占位、空函数、空文档章节或仅描述未实现行为的伪代码。确属抽象基类契约或既有代码所需的 `NotImplementedError` 不在此限，但不得新增未实现的具体业务分支。
- 文档任务必须写入完整、基于实际实现的内容，不得要求用户补充截图、测试数字、架构说明、演示步骤或简历描述。
- 遇到非关键歧义时，Codex 应依据现有代码风格、最小改动和向后兼容原则自主决策，并在最终总结中记录决定，不得停下询问用户。
- 本计划不删除、不绕过、不弱化现有 LangGraph `approval_node`、Interrupt/Resume、CodeChange Accept/Reject/Revise 等 HITL 产品逻辑；除非某个任务明确要求修复其回归，否则保持业务语义不变。
- 模型永远不能看到或提交 `local_path`、绝对路径、用户目录或 Workspace 根路径。
- MCP Server、Local Tool Handler 和 CommandRunner 都必须从可信 `repository_id` 解析 Workspace。
- 不提供任意 Shell 工具；禁止 `shell=True`；禁止接收完整命令字符串。
- `workspace.delete_file` 是否暴露以及是否需要运行时确认，按本计划的安全设计执行；这属于产品安全策略，与 Codex 实施过程是否留下 HUMAN GATE 无关。
- 依赖安装命令不进入 CommandRunner 白名单；缺少依赖时返回结构化错误，不自动安装。
- 所有新服务都必须支持依赖注入或 Fake 实现，单元测试不得依赖真实 Qwen、真实 GitHub、真实 Redis 或外部向量服务。
- 保持现有 API 兼容；数据库变化必须通过 Alembic migration 完成。
- 默认配置必须能在 SQLite、本地 Redis 和 Windows 开发环境运行。
- 每个 Task 单独提交；提交信息使用 Conventional Commits。
- 不进行与本计划无关的大规模重构。

### Codex 自主完成规则

1. **代码完整：**所有新增接口都必须有可运行实现；计划中的代码片段是接口约束，不是允许保留的伪代码。
2. **测试完整：**Codex 自行创建测试数据、Fake LLM、Fake MCP Client、临时仓库和临时数据库，不要求用户提供。
3. **迁移完整：**Codex 自行生成并校验 Alembic revision，填写真实 revision ID、upgrade/downgrade 内容和索引名称。
4. **文档完整：**基线数字、测试结果、流程图、架构说明、安全模型、评测指标和演示脚本全部依据实际执行结果自动填写。
5. **配置完整：**新增配置项必须给出安全默认值、`.env.example` 说明和测试覆盖；不得要求用户决定变量名或默认值。真实密钥本身不得写入仓库。
6. **错误处理完整：**外部服务不可用时实现确定的降级或结构化失败路径，并通过 Fake/Mock 覆盖；不得以“后续人工处理”代替实现。
7. **验证完整：**Codex 必须运行可在本地执行的全部命令，修复失败后重跑，并把真实结果写入最终总结。
8. **无凭据处理：**只有真实 Qwen、GitHub 或外部服务的 live 调用可因缺少凭据标记为 `NOT RUN`；相关业务代码、契约测试、Fake 集成测试和文档仍须完成。

## 本轮范围

本轮必须完成：

1. Planner Structured Output；
2. 旧文件文本协议退出主路径；
3. MCP Bearer Token 校验和仓库级授权；
4. 受限 Command Runner 和自动 Verification；
5. Workspace Code RAG 与混合检索；
6. Context Engineering 装配层；
7. Agent Evaluation；
8. 文档、流程图和简历可用的技术说明。

本轮明确不做：

- PostgreSQL LangGraph Checkpointer；
- Orchestrator 子任务独立 Celery 化；
- Git worktree 并行执行；
- 完整长期记忆平台；
- MCP 动态工具自动注册；
- 任意 Shell、依赖安装、文件删除；
- 前端大改版。

---

## 目标目录结构

```text
app/
├── agents/
│   ├── context/
│   │   ├── __init__.py
│   │   ├── assembler.py
│   │   ├── models.py
│   │   └── token_budget.py
│   └── graph/
│       ├── nodes.py
│       └── schemas.py
├── mcp/
│   ├── auth.py
│   ├── client.py
│   ├── repository_resolver.py
│   └── workspace_server.py
├── models/
│   └── code_chunk.py
├── rag/
│   ├── __init__.py
│   ├── chunking.py
│   ├── embeddings.py
│   ├── index_service.py
│   ├── models.py
│   └── retrieval.py
├── services/
│   ├── command_runner.py
│   └── verification_service.py
├── tools/
│   ├── command_tools.py
│   ├── rag_tools.py
│   └── workspace_tools.py
└── workers/
    └── index_tasks.py

evals/
├── __init__.py
├── cases/
│   ├── planner_cases.jsonl
│   └── retrieval_cases.jsonl
├── metrics.py
├── report.py
└── run.py

tests/
├── test_agent_context_assembler.py
├── test_agent_evaluation.py
├── test_command_runner.py
├── test_langgraph_planner_structured_output.py
├── test_mcp_authorization.py
├── test_rag_chunking.py
├── test_rag_index_service.py
├── test_rag_retrieval.py
└── test_verification_service.py
```

---

### Task 0：建立基线并固定依赖边界

**Files：**

- Modify: `requirements.txt`
- Modify: `README.md`
- Create: `docs/agenthub-improvement-baseline.md`

**目标：** 在修改核心链路之前记录可重复的基线，并避免 MCP SDK 自动升级导致 API 不兼容。

- [ ] **Step 1：运行当前后端测试并记录结果**

```powershell
$env:PYTHONPATH='.'
pytest tests -q
```

将测试数量、通过数量、失败测试和失败原因写入 `docs/agenthub-improvement-baseline.md`。如果当前存在失败测试，先确认失败属于环境问题还是代码问题；代码问题必须在进入 Task 1 前修复。

- [ ] **Step 2：运行当前前端构建**

```powershell
cd agenthub-frontend
npm run build
cd ..
```

把构建结果记录到基线文档。

- [ ] **Step 3：固定 MCP SDK v1 兼容范围并补充测试依赖**

将 `requirements.txt` 中：

```text
mcp[cli]
```

替换为：

```text
mcp[cli]>=1.27,<2
```

新增：

```text
numpy>=2.1,<3
```

不要在本轮升级到 MCP SDK v2。

- [ ] **Step 4：更新 README 的安装说明**

说明 MCP SDK 被固定在 v1，是为了保持当前 `FastMCP`、`ClientSession` 和 `streamable_http_client` 调用方式稳定。

- [ ] **Step 5：重新安装并验证**

```powershell
pip install -r requirements.txt
$env:PYTHONPATH='.'
pytest tests -q
```

- [ ] **Step 6：提交**

```bash
git add requirements.txt README.md docs/agenthub-improvement-baseline.md
git commit -m "chore: establish agent hardening baseline"
```

**验收：** 全量测试不低于修改前基线，前端可构建，MCP 依赖具有明确上限。

---

### Task 1：Planner 使用 Pydantic Structured Output

**Files：**

- Create: `app/agents/graph/schemas.py`
- Modify: `app/agents/graph/nodes.py`
- Modify: `app/agents/llm_factory.py` only if required for test injection
- Create: `tests/test_langgraph_planner_structured_output.py`

**Interfaces：**

```python
class PlanStep(BaseModel):
    agent: Literal["backend", "frontend", "reviewer"]
    instruction: str

class OrchestratorPlan(BaseModel):
    steps: list[PlanStep]

async def generate_orchestrator_plan(
    llm: Any,
    user_goal: str,
    *,
    max_attempts: int = 2,
) -> OrchestratorPlan: ...
```

**行为要求：**

- `instruction` 去除首尾空白后不能为空；
- `steps` 至少一个，最多十二个；
- 非法 Agent 不得静默改成 backend；
- 结构化调用失败最多重试两次；
- 两次失败后使用单步骤安全兜底计划；
- 失败日志记录异常类型和验证错误，但不记录 API Key；
- `plan_node` 只消费 `OrchestratorPlan`，不再自行正则截取 JSON。

- [ ] **Step 1：为 Schema 写失败测试**

覆盖：合法计划、空步骤、空 instruction、非法 agent、步骤超过十二个。

```powershell
pytest tests/test_langgraph_planner_structured_output.py -q
```

预期：因为 Schema 尚未创建而失败。

- [ ] **Step 2：实现 Schema**

`app/agents/graph/schemas.py` 使用 `Literal`、`Field(min_length=1, max_length=12)` 和 `field_validator` 实现严格校验。

- [ ] **Step 3：为结构化调用重试和 fallback 写测试**

创建 Fake LLM：

1. 第一次抛出 Pydantic 验证错误，第二次返回合法计划；
2. 两次都失败；
3. 首次成功。

断言调用次数和最终计划。

- [ ] **Step 4：实现 `generate_orchestrator_plan`**

优先调用：

```python
structured_llm = llm.with_structured_output(OrchestratorPlan)
result = await structured_llm.ainvoke(messages)
```

若 provider 返回字典，显式执行：

```python
plan = OrchestratorPlan.model_validate(result)
```

fallback 固定为：

```python
OrchestratorPlan(
    steps=[PlanStep(agent="backend", instruction=user_goal.strip() or "Handle the user request.")]
)
```

- [ ] **Step 5：替换 `plan_node` 的正则解析**

删除 `_extract_plan`、`re` 依赖和针对 Planner 的 `json.loads`。数据库中继续保存：

```json
{
  "plan": [
    {"agent": "backend", "instruction": "..."}
  ]
}
```

以保持前端兼容。

- [ ] **Step 6：运行定向测试和 LangGraph 回归测试**

```powershell
pytest tests/test_langgraph_planner_structured_output.py -q
pytest tests/test_langgraph_runtime.py tests/test_langgraph_interrupt_resume.py tests/test_langgraph_sqlite_persistence.py -q
```

- [ ] **Step 7：提交**

```bash
git add app/agents/graph/schemas.py app/agents/graph/nodes.py tests/test_langgraph_planner_structured_output.py
git commit -m "feat: add structured orchestrator planning"
```

**验收：** Planner 不再依赖正则提取 JSON；非法结构可观测、可重试、可安全降级。

---

### Task 2：退出旧 `[FILE:]` 协议主路径

**Files：**

- Modify: `app/core/config.py`
- Modify: `app/services/code_change_service.py`
- Modify: `app/agents/graph/nodes.py`
- Modify: `app/agents/tool_calling.py`
- Modify: `README.md`
- Create: `tests/test_legacy_file_protocol_disabled.py`

**目标：** 原生 Tool Calling 成为唯一默认路径，旧协议只保留显式兼容开关。

- [ ] **Step 1：写失败测试**

断言：

- 默认 `agent_legacy_file_protocol_fallback` 为 `False`；
- Revision Task 指令要求使用 Workspace Tools，不出现 `[FILE:]`、`[DELETE:]`、`[RENAME:]`；
- Verifier 的错误信息不再要求生成 `[FILE:]` block；
- fallback 显式设置为 `True` 时，历史兼容测试仍可运行。

- [ ] **Step 2：修改默认配置**

```python
agent_legacy_file_protocol_fallback: bool = False
```

- [ ] **Step 3：修正 Revision Prompt**

将旧指令替换为：

```text
Inspect the existing workspace state and revise the rejected change.
Use the registered workspace tools for all file reads and writes.
Do not emit [FILE:], [DELETE:], or [RENAME:] markers.
Run the available verification tools before returning the final summary.
```

- [ ] **Step 4：修正 Verifier 文案**

将“没有生成 `[FILE:]` block”改为：

```text
Code was requested but the agent reported no changed files.
```

Task 4 完成后，此规则将被真实 VerificationService 替代。

- [ ] **Step 5：README 标记迁移状态**

说明旧协议仅用于读取历史响应或显式兼容，不是默认执行路径。

- [ ] **Step 6：测试并提交**

```powershell
pytest tests/test_legacy_file_protocol_disabled.py tests/test_agent_native_tool_calling.py -q
```

若实际原生 Tool Calling 测试文件名不同，使用仓库中现有对应测试文件，不创建重复测试套件。

```bash
git add app/core/config.py app/services/code_change_service.py app/agents/graph/nodes.py app/agents/tool_calling.py README.md tests/test_legacy_file_protocol_disabled.py
git commit -m "refactor: make native tool calling the default"
```

**验收：** 默认运行中模型文本不会触发文件写入；Revision 与 Verifier 不再依赖旧 marker。

---

### Task 3：MCP 身份验证和仓库级授权

**Files：**

- Create: `app/mcp/auth.py`
- Create: `app/mcp/repository_resolver.py`
- Modify: `app/mcp/workspace_server.py`
- Modify: `app/mcp/client.py`
- Modify: `app/tools/base.py`
- Modify: `app/tools/registry.py`
- Modify: `app/tools/workspace_tools.py`
- Modify: `app/agents/base.py`
- Modify: `app/agents/qwen_adapter.py`
- Modify: `app/agents/tool_calling.py`
- Modify: `app/workers/agent_tasks.py`
- Modify: `app/core/config.py`
- Create: `tests/test_mcp_authorization.py`

**Interfaces：**

```python
class ToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any]
    task_id: int | None
    conversation_id: int | None
    user_id: int | None
    repository_id: int | None
    require_confirmation: bool = False

class AgentRunRequest(BaseModel):
    task_id: int
    conversation_id: int
    instruction: str
    repo_path: str | None
    repository_id: int | None
    user_id: int | None
    ...

@dataclass(frozen=True)
class ResolvedWorkspace:
    repository_id: int
    user_id: int
    local_path: str

class RepositoryResolver:
    def resolve_owned_workspace(
        self,
        repository_id: int,
        user_id: int,
    ) -> ResolvedWorkspace: ...
```

**安全模型：**

```text
LLM Tool Call
→ model schema 中不含 local_path/user_id/repository_id
→ AgentHub 从 Conversation/Repository 注入 user_id + repository_id
→ ToolRegistry
→ MCP Client 使用 Bearer Token 调用 Server
→ MCP Tool 收到 user_id + repository_id
→ RepositoryResolver 查询数据库并校验 Repository.user_id
→ WorkspaceService 再做路径边界校验
```

- [ ] **Step 1：写 MCP 鉴权失败测试**

使用 Starlette/FastAPI `TestClient` 或 `httpx.ASGITransport` 测试：

- 无 Authorization Header 返回 401；
- 非 Bearer 格式返回 401；
- Token 不匹配返回 401；
- 正确 Token 可以访问 MCP endpoint；
- 比较 Token 使用 `hmac.compare_digest`。

- [ ] **Step 2：实现 Bearer Token ASGI Middleware**

`app/mcp/auth.py`：

```python
class InternalBearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str): ...
    async def __call__(self, scope, receive, send): ...
```

要求：

- 只接受 `Authorization: Bearer <token>`；
- Token 为空时 Server 拒绝启动，而不是无鉴权运行；
- 日志不打印 Token；
- 返回 JSON 401 响应。

- [ ] **Step 3：把 MCP Server 改为显式 ASGI App**

使用：

```python
mcp = FastMCP(
    "AgentHub Workspace MCP",
    host=_host,
    port=_port,
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
)
```

创建 Starlette App，将 `mcp.streamable_http_app()` 挂载到配置路径，并在 lifespan 中运行 `mcp.session_manager.run()`。最外层包装 `InternalBearerAuthMiddleware`。模块暴露：

```python
app
mcp
```

方便测试和 `uvicorn app.mcp.workspace_server:app` 启动。

- [ ] **Step 4：实现仓库归属解析**

`RepositoryResolver.resolve_owned_workspace`：

1. `repository_id`、`user_id` 必须为正整数；
2. 查询 `Repository`；
3. 不存在时抛出 `WorkspaceAuthorizationError("Repository not found")`；
4. `repository.user_id != user_id` 时抛出 `WorkspaceAuthorizationError("Repository access denied")`；
5. `local_path` 为空或目录不存在时抛出明确错误；
6. 返回不可变 `ResolvedWorkspace`。

- [ ] **Step 5：扩展请求上下文并贯通调用链**

从 Worker 中已有的 `Conversation.repository_id → Repository` 查询结果提取：

```python
repository_id=repo.id
user_id=repo.user_id
```

依次传入：

```text
AgentRunRequest
→ QwenAgentAdapter
→ run_tool_calling_agent
→ ToolCallRequest
→ ToolRegistry._call_mcp
→ MCPToolClient.call_tool
```

- [ ] **Step 6：模型 Schema 隐藏可信参数**

`_model_input_schema` 必须删除：

```python
{"local_path", "repository_id", "user_id"}
```

模型请求参数中即使伪造这三个字段，也必须在调用前被后端可信值覆盖。

- [ ] **Step 7：修改 MCP Workspace Tools**

所有工具签名由：

```python
workspace_read_file(local_path: str, target_file: str)
```

改为：

```python
workspace_read_file(
    repository_id: int,
    user_id: int,
    target_file: str,
)
```

工具内部先调用 Resolver，再把可信 `resolved.local_path` 传给 WorkspaceService。

`workspace_delete_file` 不注册到 MCP Server。

- [ ] **Step 8：修改 Local Workspace Tools**

Local Tool Handler 也优先使用 `request.repository_id` 和 `request.user_id` 解析路径。为了兼容现有内部调用，可暂时保留 `request.arguments["local_path"]` 的只读 fallback，但满足以下条件：

- 仅在 `mcp_enabled=False` 且请求由内部代码构造时允许；
- 模型 Schema 中永远不可见；
- 记录兼容路径使用日志；
- 新测试必须只使用 repository_id/user_id。

- [ ] **Step 9：测试越权和伪造参数**

覆盖：

- 用户 A 访问用户 B Repository 被拒绝；
- 不存在 Repository 被拒绝；
- 模型 arguments 中伪造 `local_path` 会被移除；
- 模型 arguments 中伪造 `repository_id`、`user_id` 会被可信值覆盖；
- `workspace.delete_file` 不出现在模型工具列表和 MCP `list_tools` 中；
- 合法用户可读、写自己的仓库文件；
- 路径穿越仍被 WorkspaceService 拒绝。

- [ ] **Step 10：运行测试并提交**

```powershell
pytest tests/test_mcp_authorization.py tests/test_workspace_security.py tests/test_agent_native_tool_calling.py -q
```

```bash
git add app/mcp app/tools app/agents app/workers/agent_tasks.py app/core/config.py tests/test_mcp_authorization.py
git commit -m "feat: enforce repository-scoped mcp authorization"
```

**验收：** 直接调用 MCP Server 必须经过 Token；任何工具都不能信任客户端路径；跨用户仓库访问被拒绝；高风险删除工具不可发现、不可调用。

---

### Task 4：实现受限 Command Runner

**Files：**

- Create: `app/services/command_runner.py`
- Create: `app/tools/command_tools.py`
- Modify: `app/tools/__init__.py`
- Modify: `app/agents/tool_calling.py`
- Modify: `app/core/config.py`
- Create: `tests/test_command_runner.py`

**Interfaces：**

```python
class CommandKind(str, Enum):
    PYTEST = "pytest"
    RUFF_CHECK = "ruff_check"
    MYPY = "mypy"
    NPM_TEST = "npm_test"
    NPM_BUILD = "npm_build"
    PNPM_TEST = "pnpm_test"
    PNPM_BUILD = "pnpm_build"
    YARN_TEST = "yarn_test"
    YARN_BUILD = "yarn_build"

class CommandExecutionResult(BaseModel):
    command_kind: CommandKind
    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    truncated: bool
    success: bool

class CommandRunner:
    def run(
        self,
        *,
        workspace_path: str,
        command_kind: CommandKind,
        target: str | None = None,
    ) -> CommandExecutionResult: ...
```

**白名单：**

```python
{
    CommandKind.PYTEST: ["pytest", "-q"],
    CommandKind.RUFF_CHECK: ["ruff", "check", "."],
    CommandKind.MYPY: ["mypy", "app"],
    CommandKind.NPM_TEST: ["npm", "test", "--", "--runInBand"],
    CommandKind.NPM_BUILD: ["npm", "run", "build"],
    CommandKind.PNPM_TEST: ["pnpm", "test"],
    CommandKind.PNPM_BUILD: ["pnpm", "run", "build"],
    CommandKind.YARN_TEST: ["yarn", "test"],
    CommandKind.YARN_BUILD: ["yarn", "build"],
}
```

没有对应配置文件、script 或可执行文件时，返回结构化失败，不执行替代命令。

- [ ] **Step 1：写安全性失败测试**

覆盖：

- 不存在的 `CommandKind` 被 Pydantic/Enum 拒绝；
- target 包含 `..`、绝对路径、盘符时被拒绝；
- 无法传入 `; rm -rf`、`&&`、`|` 等 Shell 内容；
- `shell=False`；
- cwd 永远为 Resolver 返回的 Workspace；
- 环境变量中不包含 `ALIYUN_API_KEY`、`GITHUB_TOKEN`、`SECRET_KEY`、`MCP_INTERNAL_TOKEN`；
- stdout/stderr 超过上限会截断；
- 超时后进程树被终止。

- [ ] **Step 2：新增配置**

```python
agent_command_timeout_seconds: int = 120
agent_command_max_output_chars: int = 50_000
agent_command_allowed_env: str = "PATH,PYTHONPATH,HOME,USERPROFILE,TEMP,TMP,SYSTEMROOT,COMSPEC"
```

- [ ] **Step 3：实现 CommandRunner**

要求：

- 使用 `subprocess.Popen(argv, shell=False, cwd=..., env=filtered_env, ...)`；
- Windows 使用 `CREATE_NEW_PROCESS_GROUP`，超时执行 `taskkill /F /T /PID`；
- POSIX 使用 `start_new_session=True`，超时执行 `os.killpg`；
- 输出按配置截断；
- 返回结构化结果，不直接抛出非零退出码异常；
- 只有参数验证错误和运行器内部错误抛异常。

- [ ] **Step 4：实现命令工具**

注册四个模型工具：

```text
workspace.run_tests
workspace.run_lint
workspace.run_type_check
workspace.run_build
```

每个工具只接收：

```json
{
  "target": "可选的仓库内相对路径"
}
```

后端根据仓库文件自动选择具体 `CommandKind`：

- Python 测试：pytest；
- Python lint：仅存在 Ruff 配置或可执行文件时运行 ruff；
- Python type check：仅存在 mypy 配置时运行 mypy；
- 前端根据 lockfile 选择 npm/pnpm/yarn；
- package.json 缺少对应 script 时返回失败。

- [ ] **Step 5：配置 Agent Profile**

- backend：tests、lint、type_check；
- frontend：tests、lint、build；
- reviewer：tests、lint、type_check、build；
- qwen：根据仓库类型开放全部四个逻辑工具。

- [ ] **Step 6：确保审计日志完整**

ToolRegistry 现有审计记录中必须包含：

- command kind；
- target；
- exit code；
- duration；
- timed_out；
- truncated；
- success。

不得保存完整环境变量。

- [ ] **Step 7：运行测试并提交**

```powershell
pytest tests/test_command_runner.py tests/test_tool_registry.py -q
```

```bash
git add app/services/command_runner.py app/tools/command_tools.py app/tools/__init__.py app/agents/tool_calling.py app/core/config.py tests/test_command_runner.py
git commit -m "feat: add restricted agent command runner"
```

**验收：** Agent 能运行明确的测试、Lint、Type Check 和 Build，但无法构造任意命令、切换 cwd、读取敏感环境变量或安装依赖。

---

### Task 5：用真实 VerificationService 替换规则化 Verifier

**Files：**

- Create: `app/services/verification_service.py`
- Modify: `app/agents/graph/schemas.py`
- Modify: `app/agents/graph/nodes.py`
- Modify: `app/agents/graph/state.py`
- Create: `tests/test_verification_service.py`

**Interfaces：**

```python
class VerificationCheck(BaseModel):
    name: str
    success: bool
    exit_code: int | None
    summary: str
    duration_ms: int

class VerificationResult(BaseModel):
    success: bool
    checks: list[VerificationCheck]
    failure_summary: str | None = None

class VerificationService:
    def verify(
        self,
        *,
        repository_id: int,
        user_id: int,
        changed_files: list[str],
        instruction: str,
    ) -> VerificationResult: ...
```

- [ ] **Step 1：写项目类型识别测试**

覆盖：

- Python 仓库选择 pytest；
- React/Vite 仓库选择 package manager build；
- 同时包含后端和前端时运行两类检查；
- 无测试配置时返回“无适用检查”，而不是假成功；
- changed_files 只涉及文档时不运行昂贵 build。

- [ ] **Step 2：实现检查选择器**

规则：

- `*.py` 或 `tests/` 变化：pytest；
- `pyproject.toml` 含 Ruff 配置：ruff；
- `mypy.ini` 或 pyproject 含 mypy 配置：mypy；
- `package.json` 或前端源码变化：对应 package manager build；
- package.json 含 test script：增加 test；
- 仅 Markdown/文本变化：返回一个成功的 `documentation_only` check。

- [ ] **Step 3：实现 VerificationService**

顺序执行选择出的检查；为避免共享 Workspace 并发冲突，本轮不并行运行命令。任一必需检查失败，则 `VerificationResult.success=False`，并把最后 4,000 字符错误摘要写入 `failure_summary`。

- [ ] **Step 4：扩展 LangGraph State**

增加：

```python
verification_results: Annotated[list[dict[str, Any]], operator.add]
```

名称和 reducer 方式必须与现有 `execution_results` 风格一致。

- [ ] **Step 5：改造 `verify_node`**

删除字符串关键字和 `SyntaxError` 文本判断，改为：

```text
读取当前 execution_result.changed_files
→ VerificationService.verify
→ 成功：推进 current_step_index
→ 失败：errors 追加结构化失败摘要
→ LangGraph 现有错误回路让 Agent 下一轮修复
```

为防止无限修复，沿用现有图中的重试上限；若图中没有明确上限，新增 `verification_attempts`，单步骤最多两次自动修复。

- [ ] **Step 6：测试自动修复上下文**

断言 Verification 失败后，下一次 `execute_node` 的 `context["previous_error"]` 包含失败命令、退出码和截断后的 stderr。

- [ ] **Step 7：运行测试并提交**

```powershell
pytest tests/test_verification_service.py tests/test_langgraph_runtime.py -q
```

```bash
git add app/services/verification_service.py app/agents/graph tests/test_verification_service.py
git commit -m "feat: verify agent changes with real commands"
```

**验收：** Orchestrator 不再通过文本猜测代码是否正确，而是使用真实测试、Lint、类型检查或构建结果决定推进或修复。

---

### Task 6：建立 Workspace Code RAG 数据模型和分块器

**Files：**

- Create: `app/models/code_chunk.py`
- Modify: `app/models/__init__.py`
- Create: `app/rag/__init__.py`
- Create: `app/rag/models.py`
- Create: `app/rag/chunking.py`
- Create: `alembic/versions/<revision>_add_code_chunks.py`
- Create: `tests/test_rag_chunking.py`

**数据库模型：**

```python
class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id: Mapped[int]
    repository_id: Mapped[int]
    file_path: Mapped[str]
    language: Mapped[str]
    symbol_name: Mapped[str | None]
    chunk_type: Mapped[str]
    start_line: Mapped[int]
    end_line: Mapped[int]
    content: Mapped[str]
    content_hash: Mapped[str]
    commit_hash: Mapped[str | None]
    embedding_json: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
```

唯一约束：

```text
(repository_id, file_path, content_hash, start_line, end_line)
```

索引：

```text
repository_id
(repository_id, file_path)
(repository_id, content_hash)
```

- [ ] **Step 1：写 Python 分块测试**

给定包含 module docstring、两个函数和一个类的 Python 文件，断言：

- 每个函数和类独立 chunk；
- 行号准确；
- symbol_name 准确；
- 超长函数按最大行数二次切分；
- 语法错误文件进入通用行窗口分块。

- [ ] **Step 2：写 TS/JS/Markdown 通用分块测试**

通用策略：80 行窗口、15 行重叠；Markdown 优先按标题章节切分，章节超长再按窗口切分。

- [ ] **Step 3：实现 `CodeChunkDraft` 和 Chunker**

```python
class CodeChunkDraft(BaseModel):
    file_path: str
    language: str
    symbol_name: str | None
    chunk_type: str
    start_line: int
    end_line: int
    content: str
    content_hash: str

class WorkspaceChunker:
    def chunk_file(self, file_path: str, content: str) -> list[CodeChunkDraft]: ...
```

支持扩展名：`.py`、`.js`、`.jsx`、`.ts`、`.tsx`、`.java`、`.go`、`.md`、`.txt`、`.json`、`.yaml`、`.yml`。

跳过：`.git`、`.env`、锁文件、二进制文件、`node_modules`、`.venv`、`dist`、`build`、`__pycache__`、超过 500 KB 的文件。

- [ ] **Step 4：实现 SQLAlchemy Model 和 Migration**

`embedding_json` 在 SQLite 中保存 JSON 数组字符串；本轮不要求 pgvector。

- [ ] **Step 5：执行迁移和测试**

```powershell
$env:PYTHONPATH='.'
alembic upgrade head
pytest tests/test_rag_chunking.py -q
```

- [ ] **Step 6：提交**

```bash
git add app/models app/rag alembic/versions tests/test_rag_chunking.py
git commit -m "feat: add repository code chunk model"
```

**验收：** 支持稳定、带行号和符号信息的代码分块；SQLite 可保存索引数据。

---

### Task 7：实现 Embedding、索引和增量更新

**Files：**

- Create: `app/rag/embeddings.py`
- Create: `app/rag/index_service.py`
- Create: `app/workers/index_tasks.py`
- Modify: `app/workers/celery_app.py` if task autodiscovery requires
- Modify: `app/services/repo_service.py`
- Modify: `app/workers/agent_tasks.py`
- Modify: `app/core/config.py`
- Create: `tests/test_rag_index_service.py`

**Interfaces：**

```python
class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...

class HashEmbeddingProvider:
    ...  # tests and local fallback only

class OpenAICompatibleEmbeddingProvider:
    ...

class RepositoryIndexService:
    async def index_repository(self, repository_id: int) -> IndexSummary: ...
    async def update_files(self, repository_id: int, file_paths: list[str]) -> IndexSummary: ...
    def delete_file_chunks(self, repository_id: int, file_path: str) -> int: ...
```

- [ ] **Step 1：新增配置**

```python
embedding_provider: str = "hash"  # hash | openai_compatible
embedding_model: str = "text-embedding-v4"
embedding_base_url: str | None = None
embedding_api_key: str | None = None
embedding_dimensions: int = 256
rag_chunk_batch_size: int = 32
```

默认使用 `hash`，确保无外部密钥也能运行和演示；生产或 live eval 可切换兼容 Embedding API。

- [ ] **Step 2：实现可重复 Hash Embedding**

要求：

- 同一文本结果稳定；
- 输出固定维度；
- 向量 L2 归一化；
- 不使用 Python 内置 `hash()`，因为不同进程种子不稳定；
- 使用 SHA-256 token hashing。

- [ ] **Step 3：实现 OpenAI Compatible Provider**

使用 `httpx.AsyncClient` 请求：

```text
POST {embedding_base_url}/embeddings
Authorization: Bearer <embedding_api_key>
```

Body：

```json
{
  "model": "<embedding_model>",
  "input": ["text1", "text2"]
}
```

处理超时、401、429、5xx、返回数量不匹配和维度不一致；错误信息不包含密钥。

- [ ] **Step 4：写索引幂等测试**

覆盖：

- 首次全量索引；
- 同内容重复索引不创建重复 chunk；
- 文件内容变化时删除旧 chunk 并写入新 chunk；
- 文件删除时清理旧 chunk；
- repository_id 隔离；
- embedding provider 失败时事务回滚，不留下半批数据。

- [ ] **Step 5：实现 RepositoryIndexService**

全量索引流程：

```text
RepositoryResolver
→ list_files
→ read_file
→ chunk_file
→ content_hash 去重
→ batch embedding
→ 单批事务写入
→ 清理已不存在文件的 chunks
```

增量更新只处理 `changed_files`。

- [ ] **Step 6：新增 Celery 索引任务**

```python
@celery_app.task(name="app.workers.index_tasks.index_repository_task")
def index_repository_task(repository_id: int): ...

@celery_app.task(name="app.workers.index_tasks.update_repository_files_task")
def update_repository_files_task(repository_id: int, file_paths: list[str]): ...
```

失败时记录日志并允许 Celery 自动重试两次，使用指数退避；不影响主任务 CodeChange 的成功状态。

- [ ] **Step 7：接入生命周期**

- Repository clone 成功后投递全量索引；
- Agent Task 成功且 `changed_files` 非空后投递增量索引；
- Revision 成功后同样增量索引；
- 索引任务不得直接操作 Git 分支。

- [ ] **Step 8：测试并提交**

```powershell
pytest tests/test_rag_index_service.py -q
```

```bash
git add app/rag app/workers/index_tasks.py app/workers/celery_app.py app/services/repo_service.py app/workers/agent_tasks.py app/core/config.py tests/test_rag_index_service.py
git commit -m "feat: index repository code for semantic retrieval"
```

**验收：** 仓库创建后可自动建立索引；文件变化后增量更新；索引失败不会污染业务事务。

---

### Task 8：实现关键词 + 向量混合检索工具

**Files：**

- Create: `app/rag/retrieval.py`
- Create: `app/tools/rag_tools.py`
- Modify: `app/tools/__init__.py`
- Modify: `app/agents/tool_calling.py`
- Create: `tests/test_rag_retrieval.py`

**Interfaces：**

```python
class RetrievedCodeChunk(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    content: str
    keyword_rank: int | None
    vector_rank: int | None
    combined_score: float

class HybridCodeRetriever:
    async def search(
        self,
        *,
        repository_id: int,
        user_id: int,
        query: str,
        top_k: int = 8,
    ) -> list[RetrievedCodeChunk]: ...
```

- [ ] **Step 1：写向量相似度测试**

使用 Fake Embedding 验证余弦相似度排序、空向量保护、维度不一致错误。

- [ ] **Step 2：写 RRF 混合排序测试**

使用 Reciprocal Rank Fusion：

```python
score += 1.0 / (60 + rank)
```

分别融合关键词结果与向量结果；同一 `file_path + start_line + end_line` 去重。

- [ ] **Step 3：实现 HybridCodeRetriever**

- 关键词候选：复用 `workspace_service.search_code`，最多 30 条；
- 向量候选：加载当前 repository 的 chunks，使用 NumPy 计算 cosine，最多 30 条；
- 混合排序后返回 `top_k`；
- `top_k` 范围 1–20；
- 单个返回 chunk 最多 8,000 字符；
- 总返回内容最多 40,000 字符；
- 强制 RepositoryResolver 归属校验。

- [ ] **Step 4：注册 `workspace.semantic_search`**

模型可见参数：

```json
{
  "query": "string",
  "top_k": 8
}
```

模型不可见参数：`repository_id`、`user_id`、`local_path`。

开放给 backend、frontend、reviewer、qwen。

- [ ] **Step 5：测试隔离和降级**

- 仓库 A 查询不到仓库 B chunk；
- 无向量索引时退化为关键词结果；
- 关键词无结果时仍返回向量结果；
- 两者都无结果时返回空列表而不是异常；
- query 为空时验证失败。

- [ ] **Step 6：测试并提交**

```powershell
pytest tests/test_rag_retrieval.py -q
```

```bash
git add app/rag/retrieval.py app/tools/rag_tools.py app/tools/__init__.py app/agents/tool_calling.py tests/test_rag_retrieval.py
git commit -m "feat: add hybrid semantic code search"
```

**验收：** Agent 可通过语义描述召回相关代码，并保留路径、行号、符号和混合评分。

---

### Task 9：建立 ContextAssembler 和 Token Budget

**Files：**

- Create: `app/agents/context/__init__.py`
- Create: `app/agents/context/models.py`
- Create: `app/agents/context/token_budget.py`
- Create: `app/agents/context/assembler.py`
- Modify: `app/agents/qwen_adapter.py`
- Modify: `app/agents/graph/nodes.py`
- Modify: `app/core/config.py`
- Create: `tests/test_agent_context_assembler.py`

**Interfaces：**

```python
class ContextSource(str, Enum):
    SYSTEM = "system"
    CURRENT_REQUEST = "current_request"
    CONVERSATION = "conversation"
    REPOSITORY = "repository"
    RETRIEVAL = "retrieval"
    EXECUTION_RESULT = "execution_result"
    ERROR = "error"

class ContextBlock(BaseModel):
    source: ContextSource
    content: str
    priority: int
    estimated_tokens: int
    metadata: dict[str, Any]

class AssembledAgentContext(BaseModel):
    blocks: list[ContextBlock]
    messages: list[BaseMessage]
    estimated_tokens: int
    truncated_blocks: list[dict[str, Any]]

class ContextAssembler:
    async def assemble(
        self,
        *,
        system_prompt: str,
        instruction: str,
        conversation_id: int,
        repository_id: int | None,
        user_id: int | None,
        previous_results: list[dict[str, Any]],
        previous_errors: list[str],
    ) -> AssembledAgentContext: ...
```

- [ ] **Step 1：新增预算配置**

```python
agent_context_max_tokens: int = 24_000
agent_context_system_tokens: int = 3_000
agent_context_conversation_tokens: int = 4_000
agent_context_retrieval_tokens: int = 10_000
agent_context_execution_tokens: int = 5_000
agent_context_response_reserve_tokens: int = 2_000
agent_context_max_retrieval_chunks: int = 8
```

预算之和不得超过 `agent_context_max_tokens`。

- [ ] **Step 2：实现 TokenEstimator**

本轮不引入特定模型 tokenizer。使用稳定近似：

```python
estimated_tokens = max(1, math.ceil(len(text) / 4))
```

接口独立，后续可替换真实 tokenizer。

- [ ] **Step 3：写优先级和截断测试**

优先级从高到低：

1. System 安全规则；
2. 当前 instruction；
3. 当前步骤错误；
4. 前序结构化执行结果；
5. RAG 相关代码；
6. Repository 摘要；
7. Conversation 摘要/最近消息。

断言低优先级内容先被截断，System 和当前需求永不被删除。

- [ ] **Step 4：实现对话加载**

从数据库读取当前 Conversation 最近 20 条 Message，按时间排序。超过 conversation budget 时保留最近消息，并生成简单抽取式摘要：保留用户约束、文件名、命令名、错误和未完成事项；本轮不调用额外 LLM 进行摘要。

- [ ] **Step 5：实现 Repository 摘要**

根据文件结构生成：

- 顶层目录；
- 检测到的语言；
- package manager；
- 测试框架；
- 构建命令；
- 当前 branch 和 commit hash。

不得包含绝对 local_path。

- [ ] **Step 6：接入 RAG**

使用当前 instruction 调用 `HybridCodeRetriever.search`。每个检索块格式：

```text
[CODE_CONTEXT path=<file> lines=<start>-<end> symbol=<symbol> score=<score>]
<content>
```

相同 content_hash 只保留一次。

- [ ] **Step 7：装配 LangChain Messages**

输出顺序：

```text
SystemMessage：安全规则 + Agent profile
HumanMessage：当前需求
SystemMessage：Repository context
SystemMessage：Retrieved code context
SystemMessage：Previous execution and errors
HumanMessage/AIMessage：保留的最近会话
```

不要把历史 ToolMessage 原样无限累积到新任务中。

- [ ] **Step 8：接入 QwenAgentAdapter**

替换当前仅由 SystemMessage + 当前 HumanMessage + previous_error 构造 messages 的方式。QwenAgentAdapter 调用 ContextAssembler，并把装配日志写入 `AgentRunResult.logs`：

```text
context_tokens=<n>
retrieval_chunks=<n>
truncated_blocks=<n>
```

- [ ] **Step 9：接入 Orchestrator 子 Agent**

`execute_node` 向 AgentRunRequest.context 提供：

- previous execution results；
- previous errors；
- plan step index；
- parent task id。

Reviewer 必须拿到完整 changed_files、Git diff 摘要和 VerificationResult。

- [ ] **Step 10：测试并提交**

```powershell
pytest tests/test_agent_context_assembler.py tests/test_langgraph_runtime.py -q
```

```bash
git add app/agents/context app/agents/qwen_adapter.py app/agents/graph/nodes.py app/core/config.py tests/test_agent_context_assembler.py
git commit -m "feat: assemble budgeted agent context"
```

**验收：** 模型输入不再由各 Adapter 临时拼接；上下文来源、优先级、Token 估算、截断和检索命中均可观测。

---

### Task 10：建立 Agent Evaluation

**Files：**

- Create: `evals/__init__.py`
- Create: `evals/cases/planner_cases.jsonl`
- Create: `evals/cases/retrieval_cases.jsonl`
- Create: `evals/metrics.py`
- Create: `evals/report.py`
- Create: `evals/run.py`
- Create: `tests/test_agent_evaluation.py`
- Modify: `README.md`

**离线指标：**

```text
planner_schema_success_rate
planner_fallback_rate
retrieval_recall_at_5
retrieval_mrr
context_truncation_rate
tool_call_success_rate
verification_pass_rate
average_tool_rounds
```

- [ ] **Step 1：创建 Planner 数据集**

至少 20 条，包括：

- 单后端任务；
- 单前端任务；
- 前后端组合；
- 包含 Reviewer；
- 模糊需求；
- 中文需求；
- 非法 agent 诱导；
- 空需求；
- 超长需求。

JSONL 字段：

```json
{
  "id": "planner-001",
  "instruction": "...",
  "expected_agents": ["backend"],
  "min_steps": 1,
  "max_steps": 3
}
```

- [ ] **Step 2：创建 Retrieval 数据集**

至少 15 条，使用本仓库真实符号作为期望目标，例如：

```json
{
  "id": "retrieval-001",
  "query": "在哪里限制模型可使用的工具",
  "expected_paths": ["app/agents/tool_calling.py"]
}
```

- [ ] **Step 3：实现指标函数**

所有指标为纯函数，可独立单元测试。MRR 定义为第一个相关结果排名的倒数；Recall@5 为 expected path 中被 Top 5 命中的比例。

- [ ] **Step 4：实现离线 Runner**

默认模式不调用真实 LLM：

```powershell
python -m evals.run --mode offline --output evals/reports/latest.json
```

Planner 使用 Fake Structured LLM；Retrieval 使用 HashEmbeddingProvider；Tool/Verification 使用 Fake CommandRunner。

可选 live 模式：

```powershell
python -m evals.run --mode live --output evals/reports/live.json
```

只有配置 API Key 时允许执行；缺失时明确退出，不把 live 失败算作代码失败。

- [ ] **Step 5：生成 Markdown 报告**

每次运行同时生成：

```text
evals/reports/latest.json
evals/reports/latest.md
```

Markdown 包含总体指标、失败 Case、期望路径、实际 Top-K、Planner fallback 原因。

- [ ] **Step 6：设置最低门槛**

Offline smoke gate：

```text
planner_schema_success_rate = 1.0
retrieval_recall_at_5 >= 0.80
context_truncation_rate <= 0.30
tool_call_success_rate = 1.0
verification_pass_rate = 1.0
```

Runner 未达到门槛时退出码为 1。

- [ ] **Step 7：测试并提交**

```powershell
pytest tests/test_agent_evaluation.py -q
python -m evals.run --mode offline --output evals/reports/latest.json
```

```bash
git add evals tests/test_agent_evaluation.py README.md
git commit -m "feat: add reproducible agent evaluation suite"
```

**验收：** 项目能够用固定数据集衡量 Planner、RAG、Context、Tool 和 Verification，而不是只通过手工演示判断效果。

---

### Task 11：补齐日志、错误与可观测性

**Files：**

- Modify: `app/tools/audit.py`
- Modify: `app/tools/registry.py`
- Modify: `app/core/logging.py`
- Modify: affected services from Tasks 1–10
- Create: `tests/test_agent_observability.py`

**统一事件字段：**

```text
event
task_id
conversation_id
user_id
repository_id
agent_code
tool_name
duration_ms
success
error_type
context_tokens
retrieval_chunks
command_exit_code
verification_success
```

- [ ] **Step 1：写日志脱敏测试**

日志中不得出现：

- `ALIYUN_API_KEY` 值；
- `GITHUB_TOKEN` 值；
- `MCP_INTERNAL_TOKEN` 值；
- Authorization Header；
- Repository 绝对路径；
- 完整文件内容。

- [ ] **Step 2：统一结构化日志 helper**

新增或扩展现有 helper：

```python
def log_agent_event(logger, event: str, **fields: Any) -> None: ...
```

字段值统一序列化，错误只记录类型、摘要和安全元数据。

- [ ] **Step 3：为关键链路增加事件**

至少：

```text
planner.started
planner.completed
planner.fallback
mcp.auth_rejected
mcp.tool_called
command.started
command.completed
rag.index_started
rag.index_completed
rag.search_completed
context.assembled
verification.completed
eval.completed
```

- [ ] **Step 4：测试并提交**

```powershell
pytest tests/test_agent_observability.py -q
```

```bash
git add app/tools/audit.py app/tools/registry.py app/core/logging.py app tests/test_agent_observability.py
git commit -m "feat: add agent execution observability"
```

**验收：** 能从日志回答“模型看到了哪些上下文、调用了什么工具、为什么验证失败、RAG 是否命中、是否发生截断”，同时不泄露敏感信息。

---

### Task 12：更新文档、流程图和演示脚本

**Files：**

- Modify: `README.md`
- Modify: `docs/flowcharts.md`
- Create: `docs/agent-architecture.md`
- Create: `docs/agent-security-model.md`
- Create: `docs/agent-evaluation.md`
- Create: `docs/demo/agent-internship-demo.md`

- [ ] **Step 1：更新系统架构图**

新增流程：

```text
User Instruction
→ ContextAssembler
  → Conversation Context
  → Repository Summary
  → Hybrid Code Retrieval
  → Previous Results
→ Qwen Native Tool Calling
→ ToolRegistry
  → Local / MCP
  → Repository Authorization
→ Workspace / Command Tools
→ VerificationService
→ Repair Loop
→ CodeChange / Review / PR
```

- [ ] **Step 2：更新安全模型**

说明：

- 为什么模型不持有 local_path；
- MCP Token 解决 Authentication；
- RepositoryResolver 解决 Authorization；
- WorkspaceService 解决路径边界；
- CommandRunner 解决命令执行边界；
- 高风险工具默认不可发现；
- 说明本文档所称 HUMAN GATE 仅指 Codex 不得把实施工作转交用户；AgentHub 运行时的 HITL、安全确认与审批机制保持独立。

- [ ] **Step 3：编写 10 分钟演示脚本**

演示顺序：

1. 创建并绑定 Repository；
2. 提交一个涉及后端代码修改的任务；
3. 展示 Structured Plan；
4. 展示 semantic_search 命中代码；
5. 展示 write_file；
6. 展示 pytest 或 build；
7. 故意制造一次失败并展示自动修复；
8. 展示 Diff 和 VerificationResult；
9. 展示审计日志；
10. 运行 offline eval 并展示指标。

- [ ] **Step 4：补充面试讲解问题**

`docs/agent-architecture.md` 最后加入：

- Structured Output 为什么比 JSON Prompt 稳定；
- Tool Calling 与 MCP 的职责区别；
- Authentication、Authorization、路径校验的区别；
- 为什么禁止任意 Shell；
- RAG 为什么使用混合检索；
- Checkpoint 与 Memory 的区别；
- Context Engineering 如何控制 Token；
- Agent 如何自动验证和修复；
- 如何评估 Agent，而不是只看 Demo。

- [ ] **Step 5：提交**

```bash
git add README.md docs
git commit -m "docs: document hardened agent architecture"
```

**验收：** 面试官不阅读全部源码，也能通过文档理解核心设计、数据流、安全边界、评测方式和演示步骤。

---

## 最终验证

Codex 完成全部 Task 后必须依次执行：

```powershell
$env:PYTHONPATH='.'
alembic upgrade head
pytest tests -q
python -m evals.run --mode offline --output evals/reports/latest.json
cd agenthub-frontend
npm run build
cd ..
```

再执行以下检查：

```powershell
git status --short
git diff --check
```

### 必须满足的最终验收标准

- [ ] 后端全量测试通过；
- [ ] 前端构建通过；
- [ ] Alembic 可从旧数据库升级到最新版本；
- [ ] Planner 不再通过正则解析 JSON；
- [ ] 默认关闭旧 `[FILE:]` fallback；
- [ ] Revision Prompt 不再要求 marker；
- [ ] MCP 无 Token 返回 401；
- [ ] MCP 工具不接收 `local_path`；
- [ ] 跨用户 Repository 访问被拒绝；
- [ ] 删除工具不可发现；
- [ ] CommandRunner 无任意命令入口；
- [ ] CommandRunner 使用 `shell=False`；
- [ ] 敏感环境变量不会传给子进程；
- [ ] Verifier 运行真实测试、Lint、Type Check 或 Build；
- [ ] 验证失败可自动进入修复回路；
- [ ] Code RAG 支持分块、增量索引和混合检索；
- [ ] 检索强制 repository/user 隔离；
- [ ] ContextAssembler 有预算、优先级、去重和截断记录；
- [ ] Offline Eval 达到设定门槛；
- [ ] 日志不包含密钥、Authorization Header、绝对 Workspace 路径或完整文件内容；
- [ ] README、流程图、安全文档、评测文档和演示脚本同步更新；
- [ ] Codex 未留下需要用户填写、选择、实现或验证的 HUMAN GATE；
- [ ] 现有 LangGraph HITL、Interrupt/Resume 和代码审核确认逻辑未因本计划被删除、绕过或弱化；
- [ ] 新增代码和文档不存在 TODO、TBD、空白章节、占位实现或要求用户后续补充的内容；
- [ ] `git diff --check` 无错误；
- [ ] 工作区没有意外生成的数据库、缓存、索引报告或密钥文件被提交。

## Codex 完成后的输出格式

执行完成后只输出以下结构，不要仅回复“已完成”。下面的 `...`、`X`、`<hash>` 只是格式示意，Codex 输出时必须全部替换为真实执行结果，不得原样保留：

```markdown
## Implementation Summary

### Completed Tasks
- Task 0: ...
- Task 1: ...

### Key Architecture Changes
- ...

### Database Migrations
- revision: ...
- tables/indexes: ...

### Verification Evidence
- pytest: X passed
- offline eval: PASS, metrics ...
- frontend build: PASS
- git diff --check: PASS

### Important Design Decisions
- ...

### Remaining Limitations
- ...

### Commits
- <hash> <message>
```

如果某项因为真实外部密钥缺失无法运行，必须明确标记为 `NOT RUN: missing external credential`，但 Offline Test、Fake Provider Test 和本地构建仍必须全部完成。
