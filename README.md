# AgentHub

AgentHub 是一个面向软件开发场景的 AI Agent 协作平台。用户可以像聊天一样提交需求，系统会创建任务、调用 Agent、修改本地 Git 工作区、生成 Diff，并支持人工确认、代码 Review、本地预览部署和 Pull Request 流程。

当前仓库包含：

- `app/`: FastAPI 后端
- `agenthub-frontend/`: React + Vite 前端
- `alembic/`: 数据库迁移
- `tests/`: 后端测试
- `docs/flowcharts.md`: 详细流程图

## 当前能力

- 用户注册、登录、JWT 鉴权
- Conversation / Message 聊天流程
- `@mock`、`@qwen`、`@orchestrator` 指令
- Celery + Redis 异步任务执行
- WebSocket 事件推送
- LangGraph Orchestrator 任务拆解
- Orchestrator 计划确认后执行
- Celery Canvas DAG/Wave 子任务编排与 Git Worktree 隔离
- SQLite / PostgreSQL LangGraph Checkpointer 可切换
- MCP `tools/list` 动态发现、Schema 清洗与安全 profile
- Agent 通过原生 LLM Tool Calling 修改真实 workspace 文件
- ToolRegistry 统一执行风险检查、审计、任务日志和 Local / MCP / Hybrid 路由
- ContextAssembler 统一 Conversation、Repository、RAG、执行结果和错误预算
- 关键词 + 向量 RRF 混合代码检索与自动增量索引
- 受限 test/lint/type/build CommandRunner 与真实 VerificationService
- 验证失败最多两次自动修复，不以文本自报成功
- 脱敏结构化 Agent 事件与可重复 offline evaluation gate
- `[FILE:]`、`[DELETE:]`、`[RENAME:]` 默认关闭，仅保留显式迁移兼容
- CodeChange 生成 Diff
- CodeChange Accept / Reject / Revise
- CodeChange 版本链：`parent_code_change_id`、`revision_index`
- 修订任务成功后自动生成新 CodeChange
- Diff 面板文件列表、版本信息和状态标记
- 规则化 Code Review
- 本地预览部署，支持静态文件和前端项目 build
- GitHub Pull Request 创建和元数据保存
- 任务取消、失败重试
- 结构化错误响应
- 请求日志和任务日志
- 登录限流、消息限流、用户并发任务限制
- Celery 任务软/硬超时
- workspace 安全限制
- 局域网访问支持

## 技术栈

后端：

- FastAPI
- SQLAlchemy 2.x
- Alembic
- SQLite 默认数据库
- Celery
- Redis
- WebSocket
- GitPython
- PyGithub
- LangGraph
- Qwen DashScope compatible API

前端：

- React
- Vite
- TypeScript
- Zustand
- Axios
- lucide-react
- react-diff-viewer-continued
- react-hot-toast

## 目录结构

```text
agenthub-backend/
├── app/
│   ├── agents/              # Agent adapter、LangGraph workflow
│   ├── api/                 # FastAPI routers
│   ├── core/                # 配置、日志、错误处理、限流、WebSocket 基础设施
│   ├── db/                  # 数据库 session/base
│   ├── models/              # SQLAlchemy models
│   ├── schemas/             # Pydantic DTO
│   ├── services/            # 业务服务
│   └── workers/             # Celery app/tasks
├── agenthub-frontend/       # 前端项目
├── alembic/                 # 数据库迁移
├── docs/flowcharts.md       # 详细流程图
├── evals/                   # 固定数据集、指标、runner 和报告
├── tests/                   # 后端测试
├── docker-compose.yml       # Redis 等依赖服务
└── README.md
```

## 环境准备

建议使用 Python 虚拟环境。

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

后端将 `mcp[cli]` 固定在 `>=1.27,<2` 的 MCP SDK v1 兼容范围内，以保持现有
`FastMCP`、`ClientSession` 和 `streamable_http_client` 调用方式稳定。本轮不升级到
MCP SDK v2；升级前需要单独验证 Server、Client 与 Streamable HTTP 的兼容性。

安装前端依赖：

```powershell
cd agenthub-frontend
npm install
```

启动 Redis：

```powershell
docker compose up -d redis
```

如果没有 Docker，也可以使用本机 Redis，只要 `.env` 里的 `REDIS_URL` 指向正确地址即可。

## 环境变量

在项目根目录创建 `.env`：

```env
APP_NAME=AgentHub Backend
APP_ENV=dev
SECRET_KEY=replace-with-a-secure-secret

DATABASE_URL=sqlite:///./agenthub.db
REDIS_URL=redis://localhost:6379/0
LOG_LEVEL=INFO

LANGGRAPH_CHECKPOINT_BACKEND=sqlite
LANGGRAPH_CHECKPOINT_PATH=./langgraph_checkpoints.sqlite3

ALIYUN_API_KEY=
ALIYUN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIYUN_MODEL=qwen-plus
ALIYUN_TIMEOUT_SECONDS=120
AGENT_TOOL_MAX_ROUNDS=8
AGENT_LEGACY_FILE_PROTOCOL_FALLBACK=true

GITHUB_TOKEN=

LOGIN_RATE_LIMIT_COUNT=5
LOGIN_RATE_LIMIT_WINDOW_SECONDS=300
MESSAGE_RATE_LIMIT_COUNT=20
MESSAGE_RATE_LIMIT_WINDOW_SECONDS=60
MAX_CONCURRENT_TASKS_PER_USER=3
TASK_SOFT_TIME_LIMIT_SECONDS=300
TASK_TIME_LIMIT_SECONDS=360
MAX_AGENT_FILE_BYTES=500000
```

说明：

- `ALIYUN_API_KEY` 为空时，真实 Qwen Agent 无法调用。
- `GITHUB_TOKEN` 为空时，创建真实 GitHub PR 会失败。
- SQLite 数据库默认写入项目根目录的 `agenthub.db`。

PostgreSQL LangGraph checkpoint 示例：

```env
LANGGRAPH_CHECKPOINT_BACKEND=postgres
LANGGRAPH_CHECKPOINT_DATABASE_URL=postgresql://agenthub:agenthub@localhost:5433/agenthub_checkpoints?sslmode=disable
LANGGRAPH_CHECKPOINT_AUTO_SETUP=true
```

## 数据库迁移

```powershell
$env:PYTHONPATH='.'
alembic upgrade head
```

如果遇到 `table has no column` 之类错误，通常是数据库没有执行最新迁移。

## 启动后端

本机访问：

```powershell
$env:PYTHONPATH='.'
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

局域网访问：

```powershell
$env:PYTHONPATH='.'
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

API 文档：

```text
http://127.0.0.1:8000/docs
```

## 启动 Celery Worker

```powershell
$env:PYTHONPATH='.'
celery -A app.workers.celery_app.celery_app worker --loglevel=info --pool=solo
```

Windows 下建议使用 `--pool=solo`。

## 启动前端

本机访问：

```powershell
cd agenthub-frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

局域网访问：

```powershell
cd agenthub-frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

前端默认会根据当前访问地址推导后端地址：

- `http://localhost:5173` -> `http://localhost:8000/api`
- `http://10.x.x.x:5173` -> `http://10.x.x.x:8000/api`

也可以显式指定：

```env
VITE_API_ORIGIN=http://10.4.163.113:8000
VITE_API_BASE_URL=http://10.4.163.113:8000/api
VITE_WS_ORIGIN=ws://10.4.163.113:8000
```

## 局域网访问

假设服务端主机 IP 是 `10.4.163.113`：

后端：

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

前端：

```powershell
cd agenthub-frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

其它主机访问：

```text
http://10.4.163.113:5173
```

如果无法访问，检查 Windows 防火墙是否放行 `8000` 和 `5173`：

```powershell
New-NetFirewallRule -DisplayName "AgentHub Backend 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
New-NetFirewallRule -DisplayName "AgentHub Frontend 5173" -Direction Inbound -Protocol TCP -LocalPort 5173 -Action Allow
```

## 常用流程

### 普通 Mock 任务

```text
@mock 帮我测试任务流程
```

流程：

```text
消息 -> 创建 Task -> Celery Worker -> Mock Agent -> task.updated -> 前端展示
```

### Qwen 修改代码

```text
@qwen 请把首页改成一个简单的登录页面，直接修改真实文件
```

要求会话已经绑定 Repository，否则 Agent 只能返回文本，无法修改 workspace 文件。

### Orchestrator 编排

```text
@orchestrator 请把当前前端项目首页改成注册页面
```

流程：

```text
生成计划 -> LangGraph HITL 等待确认 -> 用户 Confirm
-> Celery DAG/Wave -> 独立 Worktree 执行与验证
-> integration branch 合并 -> 唯一 CodeChange
```

并行 Agent 不共享 Workspace；每个 step 使用独立物理 Worktree 和 commit，Git
cherry-pick 负责最终冲突检测。详见
[Celery Orchestrator 与 Git Worktree](docs/orchestrator-celery-worktree.md)。

### CodeChange 审核

1. 任务成功后点击 `Diff`
2. 查看文件列表和 diff
3. 点击 `Review` 生成规则化 Review
4. 点击 `Accept` 或 `Reject`
5. Reject 后可填写原因并点击 `Revise`
6. 修订任务成功后会自动生成新版 CodeChange

### 本地预览部署

1. 打开 Diff
2. 点击 `Accept`
3. 点击 `Deploy`
4. 后端会检测项目类型：
   - 无 `package.json`: 静态文件复制
   - 有 build script: 安装依赖、执行 build、托管 `dist/build/out`

预览地址由后端 `/previews/...` 静态托管。

### 创建 Pull Request

1. CodeChange 必须先 `Accept`
2. 点击 `Create PR`
3. 后端执行 commit、push、GitHub API create pull request
4. 保存 PR 元数据：
   - `pr_number`
   - `html_url`
   - `state`
   - `merged`
   - `base_branch`
   - `head_branch`

## Agent 工具调用架构

Agent 修改 workspace 的主路径是原生 LLM Tool Calling：

```text
LLM Native Tool Calling
-> ToolCallRequest
-> ToolRegistry
-> Local / MCP / Hybrid
-> WorkspaceService
```

职责边界：

- LLM 只能从当前 Agent profile 暴露的结构化工具中选择。
- AgentHub 将模型安全名称映射回 ToolRegistry 内部名称。
- `local_path` 不暴露给模型，而是由服务端使用可信 `repo_path` 覆盖注入。
- ToolRegistry 负责风险检查、审计、任务日志以及 Local / MCP / Hybrid 路由。
- WorkspaceService 负责最终路径校验和真实文件操作。
- `workspace.delete_file` 属于高风险工具，当前阶段不向模型暴露。

模型工具调用结果会作为 `ToolMessage` 返回模型，直到模型生成最终文本回复。单次执行最多进行 `AGENT_TOOL_MAX_ROUNDS` 轮，默认值为 `8`。

### Legacy 文本协议 fallback

历史 `[FILE:]`、`[DELETE:]`、`[RENAME:]` 文本协议没有被删除，但默认关闭，只作为
读取历史模型响应或显式迁移场景的 temporary compatibility fallback。原生 Tool Calling
是新任务唯一的默认文件操作路径。

```text
模型没有返回 tool_calls
+ AGENT_LEGACY_FILE_PROTOCOL_FALLBACK=true
+ 回复仍包含历史 marker
-> apply_file_operations_with_tools
-> ToolRegistry
```

兼容 marker 格式如下：

````text
[FILE: relative/path]
```language
完整文件内容
```

[DELETE: relative/path]

[RENAME: old/path -> new/path]
````

默认配置：

```env
AGENT_LEGACY_FILE_PROTOCOL_FALLBACK=false
```

默认关闭时，即使普通文本回复包含历史 marker，也不会执行文件操作。只有运维人员为
历史兼容显式设置 `AGENT_LEGACY_FILE_PROTOCOL_FALLBACK=true` 时才启用 parser。新代码
不得直接调用 marker parser，现有 parser 仅用于旧模型响应或已保存工作流的迁移兼容。

安全限制：

- 禁止写入 workspace 外部路径
- 禁止绝对路径
- 禁止 Windows drive path
- 禁止 `.env`、`.git`、`.ssh`、`.aws`、`.npmrc` 等敏感路径
- 限制单文件写入大小

## 主要 API

Auth：

- `POST /api/auth/register`
- `POST /api/auth/login`

Conversation / Message：

- `GET /api/conversations`
- `POST /api/conversations`
- `DELETE /api/conversations/{id}`
- `GET /api/conversations/{id}/messages`
- `POST /api/messages`

Task：

- `GET /api/tasks?conversation_id={id}`
- `GET /api/tasks/{id}`
- `GET /api/tasks/{id}/children`
- `GET /api/tasks/{id}/plan`
- `POST /api/tasks/{id}/plan/confirm`
- `POST /api/tasks/{id}/cancel`
- `POST /api/tasks/{id}/retry`

Repository：

- `GET /api/repos`
- `POST /api/repos`

CodeChange：

- `POST /api/code-changes/generate`
- `GET /api/code-changes/{task_id}`
- `POST /api/code-changes/{id}/accept`
- `POST /api/code-changes/{id}/reject`
- `POST /api/code-changes/{id}/revise`
- `POST /api/code-changes/{id}/review`
- `GET /api/code-changes/{id}/reviews`

PR / Deploy：

- `POST /api/pull-requests`
- `GET /api/pull-requests/{task_id}`
- `POST /api/deployments`
- `GET /api/deployments/{task_id}`

WebSocket：

- `WS /ws/conversations/{id}?token={jwt}`

## WebSocket 事件

统一事件格式：

```json
{
  "event": "task.updated",
  "conversation_id": 1,
  "data": {}
}
```

常见事件：

- `message.created`
- `task.created`
- `task.updated`
- `task.log`
- `code_change.created`
- `code_change.accepted`
- `code_change.rejected`
- `code_review.created`
- `pull_request.created`
- `deployment.created`

## 错误响应

接口错误保留兼容字段 `detail`，并增加结构化错误：

```json
{
  "detail": "CodeChange must be accepted before creating a deployment.",
  "error": {
    "code": "CODE_CHANGE_INVALID_STATUS",
    "message": "CodeChange must be accepted before creating a deployment.",
    "detail": {
      "status_code": 400
    }
  }
}
```

限流错误会返回 `429 RATE_LIMITED`。

## 测试

后端测试：

```powershell
$env:PYTHONPATH='.'
pytest tests
```

前端构建：

```powershell
cd agenthub-frontend
npm run build
```

当前测试覆盖包括：

- Auth
- Conversation
- Message
- Task retry / cancel / plan confirm
- CodeChange accept / reject / revise
- Revision 自动生成 CodeChange
- CodeReview
- Deployment
- PullRequest
- Workspace 文件操作安全
- 结构化错误
- 请求日志
- 限流和并发任务限制

## 流程图

详细流程图见：

```text
docs/flowcharts.md
```

其中包含：

- 系统总览
- 消息到任务创建
- Orchestrator 计划确认
- Worker 执行
- CodeChange 审核修订
- Review
- Deploy
- Pull Request
- 安全稳定性
- 删除对话级联清理
- 局域网访问地址推导

## Git 提交建议

不要提交以下内容：

```text
.env
*.db
test.db
.pytest_cache/
__pycache__/
.venv/
previews/
workspaces/
agenthub-frontend/node_modules/
agenthub-frontend/dist/
```

提交前检查：

```powershell
git status
```

## 当前限制

- 默认数据库是 SQLite，生产环境建议换成 MySQL/PostgreSQL。
- 当前 CodeReview 是规则化扫描，后续可以接入真实 Reviewer LLM。
- PR 状态目前创建时保存，后续可以增加 GitHub 状态同步。
- 本地预览构建直接在 workspace 执行，生产环境建议改成容器隔离构建。
- 内存限流适合单进程开发环境，多实例部署应改成 Redis 限流。

## Agent evaluation

Run the deterministic offline evaluation gate without external API keys:

```powershell
python -m evals.run --mode offline --output evals/reports/latest.json
```

The command also writes `evals/reports/latest.md` and exits with status 1 when
the planner, retrieval, context, tool, or verification smoke thresholds fail.
Live evaluation is optional and is skipped cleanly when no API key is configured:

```powershell
python -m evals.run --mode live --output evals/reports/live.json
```

## Hardened Agent documentation

- [Agent architecture](docs/agent-architecture.md)
- [Agent security model](docs/agent-security-model.md)
- [Agent evaluation guide](docs/agent-evaluation.md)
- [10-minute demo script](docs/demo/agent-internship-demo.md)
- [System flowcharts](docs/flowcharts.md)
- [Celery Orchestrator 与 Git Worktree](docs/orchestrator-celery-worktree.md)
- [LangGraph PostgreSQL Checkpointer](docs/langgraph-postgres-checkpointer.md)
- [MCP 动态发现](docs/mcp-dynamic-discovery.md)

关键安全配置包括 `MCP_INTERNAL_TOKEN`、`MCP_TOOL_MODE`、受限命令超时/输出上限、Embedding provider，以及 `AGENT_CONTEXT_*` 分类预算。默认 embedding provider 为本地 deterministic hash；生产可切换 OpenAI-compatible endpoint。不要向模型参数、日志或代码仓库暴露 token 与绝对 workspace 路径。
