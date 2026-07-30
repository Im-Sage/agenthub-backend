# 10-minute Hardened Agent Demo

目标：在十分钟内展示 AgentHub 不只是“能写代码”，还具备结构化规划、权限边界、语义检索、真实验证、自动修复、审计与离线评测。

## Before the demo

- 启动 FastAPI、Redis、Celery worker 与前端。
- 准备一个可安全修改的 Python 示例仓库，包含 pytest。
- 完成数据库迁移，并确认 `MCP_INTERNAL_TOKEN` 仅存在于服务环境。
- 预先运行一次离线 eval，避免首次索引影响演示节奏。

## Timeline

### 0:00–0:45 — Create and bind Repository

在 UI 创建 Repository，展示 clone 成功后自动投递 `index_repository_task`。强调模型没有看到 `local_path`；数据库 identity 经 RepositoryResolver 解析工作区。

### 0:45–1:30 — Submit a backend change

提交：“为订单查询增加可选 status 参数，保持现有 API 兼容并补测试。”绑定 `@orchestrator`，展示父任务与 repository identity。

### 1:30–2:15 — Show Structured Plan

展示 `OrchestratorPlan`：backend 实现、reviewer 检查。说明 schema 限定 agent 与步数，非法自由文本不会被正则“猜”成计划。

### 2:15–3:00 — Show semantic_search

在工具审计或结构化日志中展示 `workspace.semantic_search`，查看命中的相对路径、行号、symbol、keyword/vector rank 和 RRF score。指出 identity 参数不在模型 schema。

### 3:00–3:45 — Show write_file

展示 native tool call 的 JSON 参数和 `workspace.write_file` 结果。强调路径 containment、文件大小限制与 ToolRegistry profile。

### 3:45–4:45 — Run pytest or build

展示 `workspace.run_tests`。解释它映射到 CommandKind allowlist：没有任意 shell，cwd 固定，环境过滤，超时和输出截断。

### 4:45–6:15 — Force one failure and auto-repair

在示例任务中预留一个会让 pytest 首次失败的断言。展示 VerificationResult 中 command、exit code 与截断 stderr 进入 `previous_error`，Agent 下一轮修复。指出最多两次自动修复，防止无限循环。

### 6:15–7:15 — Diff and VerificationResult

打开 CodeChange Diff，确认 changed files 与最终 VerificationResult。Reviewer 应看到完整 changed files、Git diff 摘要与 verification history。

### 7:15–8:00 — Audit and observability

展示 `planner.completed`、`rag.search_completed`、`mcp.tool_called`、`context.assembled`、`command.completed` 和 `verification.completed` JSON 日志。检查只有相对路径/计数/错误类型，没有 token、Authorization、prompt 或完整代码。

### 8:00–9:30 — Run offline eval

```powershell
$env:PYTHONPATH='.'
python -m evals.run --mode offline --output evals/reports/latest.json
```

打开 Markdown 报告，解释 schema success、fallback、Recall@5、MRR、context truncation、tool/verification success 和 gate。

### 9:30–10:00 — Close with boundaries

总结三层安全：MCP token 做 Authentication，RepositoryResolver 做 Authorization，WorkspaceService 做路径 containment；CommandRunner 与 VerificationService 分别限制执行能力和成功判定。最后说明 runtime HITL/审批仍保留，不等于把工程实施工作交还用户。

## Expected evidence

- Structured Plan 是 schema 对象，不是 Markdown JSON。
- semantic_search 结果包含 path/lines/symbol/score。
- Tool schema 不含 user/repository/local path。
- 首次验证失败、修复后通过。
- 审计记录 success、duration 与 error type。
- Offline runner 返回 0，JSON 与 Markdown 同时更新。
