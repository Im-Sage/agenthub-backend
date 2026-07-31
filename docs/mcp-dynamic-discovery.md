# MCP 动态发现、路由与信任

## 启动流程

FastAPI lifespan 和 Celery `worker_process_init` 都调用统一 bootstrap。后者保证每个 prefork 子进程拥有自己的 ToolRegistry：

```text
register local tools once
-> MCPToolClient.list_tools()
-> validate and sanitize
-> register remote routes
```

`local` 模式不连接 MCP；`hybrid` 模式发现失败时记录降级并保留本地工具和上次成功 route；`mcp` 且 `MCP_DYNAMIC_FAIL_CLOSED=true` 时启动失败。token 只进入 Authorization header，不写日志或工具 schema。

## Schema 与 canonical name

远程工具名必须匹配 `^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$`，description 必须非空，`inputSchema.type` 必须是 `object`，`properties` 必须是对象，`required` 若存在必须是数组。

服务端可信参数会从 properties 和 required 中移除：

```text
local_path
repository_id
user_id
worktree_path
```

`workspace_read_file` 映射为既有 canonical 名 `workspace.read_file`；其他工具映射为 `<MCP_DYNAMIC_NAMESPACE>.<remote_name>`。ToolDefinition 同时保留精确 `remote_name`，远程调用不能通过简单替换点号猜测名称。

## 风险策略

判断顺序为 denylist、allowlist、medium-risk、未分类 LOW 和危险名称默认拒绝。包含 delete/remove/rm/shell/exec 的未明确信任工具不会自动注册为模型可见工具；动态发现不自动产生 model-visible HIGH 工具。

远程 route 可以附加到已有 local canonical 工具，但不能删除 local handler。未知远程工具可以没有 local handler。刷新同一 server 是幂等快照替换并移除 stale route；不同 server 争用同一 canonical name 会失败。

## 路由与降级

- `local`：只调用 local handler。
- `mcp`：使用该 canonical name 的 route 和精确 `remote_name`。
- `hybrid`：有 local handler 时优先 local，否则走 remote route。

注册不等于自动信任。ToolRegistry 是能力目录，Agent profile 才是模型可见 allowlist。既有 `workspace.*` exact profile 保持；未知 namespaced MCP 工具只有出现在 `MCP_DYNAMIC_AGENT_PROFILES_JSON` 对应角色列表中才暴露，HIGH 风险即使被配置仍过滤。

示例：

```env
MCP_ENABLED=true
MCP_TOOL_MODE=hybrid
MCP_WORKSPACE_SERVER_URL=http://127.0.0.1:9000/mcp
MCP_INTERNAL_TOKEN=replace-with-secret
MCP_DYNAMIC_DISCOVERY_ENABLED=true
MCP_DYNAMIC_SERVER_ID=workspace
MCP_DYNAMIC_NAMESPACE=mcp.workspace
MCP_DYNAMIC_FAIL_CLOSED=false
MCP_DYNAMIC_AGENT_PROFILES_JSON={"backend":["mcp.workspace.safe_search"]}
```

真实集成验收必须启动 MCP Server，实际执行 `list_tools()`、refresh、拒绝删除类工具并调用一个只读工具。只有 fake client 的单元测试不能被报告为真实 MCP session 通过。
