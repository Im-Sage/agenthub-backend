# Agent Security Model

AgentHub 假设模型输出、仓库内容、对话历史、工具输出和远程 MCP 响应都可能不可信。安全边界由服务端确定性代码执行，不依赖 prompt 承诺。

## Trust boundaries

| Layer | Responsibility | Enforcement |
| --- | --- | --- |
| MCP authentication | 证明调用来自受信内部服务 | 启动时强制 token，constant-time Bearer 比较 |
| Repository authorization | 证明 user 拥有 repository | `RepositoryResolver.resolve_owned_workspace` |
| Path containment | 限制具体文件在 workspace 内 | `WorkspaceService.validate_path`、解析后 containment |
| Tool policy | 限制模型可发现和调用的能力 | Agent profile + ToolRegistry risk level |
| Command boundary | 限制可执行程序和参数 | CommandKind allowlist、`shell=False`、固定 cwd |
| Verification | 防止模型文本自报成功 | 真实 test/lint/type/build 结果 |

## Why the model never owns `local_path`

模型可见 schema 只包含业务参数，例如 `query`、`top_k` 或相对 `target_file`。可信的 `repository_id` 与 `user_id` 来自 Task/Conversation，不能被模型参数覆盖。服务端先验证归属，再从数据库得到 `local_path`。因此 prompt injection 不能通过伪造绝对路径选择其他仓库。

本地工具、MCP 服务、RAG、ContextAssembler、VerificationService 与 CommandRunner 都沿用同一归属解析规则。跨仓库 ID、缺失 identity、未配置 workspace 或不存在目录均拒绝。

## MCP authentication and authorization

`MCP_INTERNAL_TOKEN` 只解决 Authentication：它表明请求来自知道内部 token 的调用方。它不代表该调用方能访问所有仓库。

每次工具调用仍必须携带服务端注入的 `repository_id/user_id`，由 RepositoryResolver 完成 Authorization。被拒绝的认证和工具调用只记录错误类型，不记录 Authorization header 或 token。

## Filesystem and command controls

WorkspaceService 只接受仓库相对路径，拒绝绝对路径、`..` 逃逸、越界 symlink 和目录型删除。模型不能通过语义搜索得到绝对路径。

CommandRunner 不提供通用 shell。可用能力是 pytest、Ruff、mypy，以及 npm/pnpm/yarn 的 test/build 枚举。执行策略包括：

- argv 列表与 `shell=False`；
- cwd 固定为已授权 workspace；
- 只继承允许的环境变量；
- target 只能是仓库相对路径；
- 超时后终止整个进程树；
- stdout/stderr 有上限；
- 不允许任意脚本名或 package script。

## Tool visibility and confirmation

Agent profile 是 deny-by-default allowlist。backend、frontend、reviewer 和 qwen 只看到完成职责所需的工具。高风险工具需要运行时确认；未授权或需确认的工具不会因 prompt 指令而放宽。

本文档中的 “HUMAN GATE” 需区分两个层面：

- 实施计划所说“不设置 HUMAN GATE”，是指 Codex 执行本项目任务时不得把本可自动完成的实施工作转交给用户。
- AgentHub 运行时的 HITL、计划确认、安全确认、CodeChange Accept/Reject 与审批机制是产品安全边界，保持独立且继续生效。

## Secrets and logs

统一日志 helper 会递归脱敏 token、password、secret、api key、Authorization/header、prompt、完整 content 与绝对路径。错误日志保存 `error_type`、短摘要和安全元数据。审计表参数与 structured result 使用相同脱敏规则。

不要把 `.env`、数据库文件、workspace、preview、构建产物或 API key 提交到 Git。生产环境应使用独立 secrets manager、TLS、token rotation、最小权限 worker 和隔离构建环境。

## Residual risks

- Hash embedding 适合离线演示，不提供真正语义理解；生产可切换受信兼容 API。
- SQLite 与本地 workspace 适合单机开发；多租户生产应增加进程/容器隔离和数据库级策略。
- 受限命令仍会运行仓库测试代码；不可信仓库应在无网络、低权限容器中执行。
- Prompt injection 不能提升确定性工具权限，但仍可能影响模型选择；Verifier、审计与人工审批承担纵深防御。
