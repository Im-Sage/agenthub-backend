# AgentHub 后续优化大纲

## 阶段 1：整理当前 v0.1 版本

目标：让当前版本稳定、清晰、可演示。

### 1.1 补充 README

需要写清楚：

```
项目简介
技术栈
启动方式
环境变量
接口说明
当前已完成功能
后续计划
```

### 1.2 整理项目结构

重点检查：

```
api 层只负责请求/响应
service 层负责业务逻辑
agents 层负责 Agent 执行
models 层只放数据库模型
schemas 层只放 Pydantic DTO
```

### 1.3 统一状态枚举

现在状态是字符串，后续建议统一管理：

```
class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
```

同理可以整理：

```
MessageType
SenderType
CodeChangeStatus
DeploymentStatus
AgentAdapterType
```

### 1.4 补充基础测试

优先写：

```
注册测试
登录测试
创建会话测试
发送普通消息测试
发送 @mock 消息测试
发送 @orchestrator 消息测试
```

------

# 阶段 2：把 BackgroundTasks 升级为 Celery

目标：让 Agent 执行真正变成异步任务，而不是依赖 FastAPI 进程后台任务。

## 2.1 引入依赖

新增：

```
celery
redis
```

## 2.2 新增目录

```
app/workers/
├── celery_app.py
└── agent_tasks.py
```

## 2.3 改造消息流程

当前：

```
POST /api/messages
→ BackgroundTasks.add_task(run_xxx_task)
```

改成：

```
POST /api/messages
→ 创建 Task
→ celery_app.send_task(...)
→ 立即返回 Message
→ Worker 后台执行任务
```

## 2.4 Celery 任务设计

建议先做 2 个 Celery 任务：

```
run_single_agent_task
run_orchestrator_task
```

后续再扩展：

```
generate_code_change_task
create_pull_request_task
create_deployment_task
```

## 2.5 注意点

Celery Worker 不能直接使用当前进程内的 `websocket_manager`。

所以阶段 2 可以先做：

```
Celery 执行任务
数据库记录状态
前端通过 HTTP 轮询任务状态
```

等阶段 3 再做 Redis Pub/Sub 实时推送。

------

# 阶段 3：WebSocket 推送升级

目标：让 Celery Worker 执行任务后，前端仍然能实时收到状态更新。

## 3.1 引入 Redis Pub/Sub

设计为：

```
Celery Worker
→ 发布事件到 Redis channel
→ FastAPI WebSocket 服务订阅 Redis channel
→ 推送给对应 conversation_id 的客户端
```

## 3.2 统一事件格式

建议所有 WebSocket 事件都采用统一格式：

```
{
  "event": "task.updated",
  "conversation_id": 1,
  "data": {}
}
```

事件类型：

```
connection.ready
message.created
task.created
task.updated
code_change.created
pull_request.created
deployment.created
agent.log
agent.error
```

## 3.3 新增事件服务

```
app/services/event_service.py
```

职责：

```
publish_event
broadcast_event
build_message_event
build_task_event
build_code_change_event
```

这样不要在每个 api 文件里直接调用 websocket_manager。

------

# 阶段 4：让 Agent 真正修改代码

目标：从“演示 Diff”升级到“真实代码变更”。

## 4.1 扩展 AgentRunRequest

建议改成：

```
class AgentRunRequest(BaseModel):
    task_id: int
    conversation_id: int
    instruction: str
    repo_path: str | None = None
    branch_name: str | None = None
    target_files: list[str] = []
    context: dict = Field(default_factory=dict)
```

## 4.2 改造 Diff 生成流程

当前：

```
生成 agenthub_changes/task_xxx.md
→ git diff
```

后续：

```
准备 workspace
→ 创建 agent-task-{task_id} 分支
→ Agent 修改真实代码
→ git diff
→ 保存 CodeChange
```

## 4.3 新增 WorkspaceService

```
app/services/workspace_service.py
```

职责：

```
clone_repository
prepare_branch
clean_workspace
get_diff
get_changed_files
commit_changes
```

## 4.4 新增安全限制

Agent 修改代码前建议限制：

```
只能修改当前 workspace
禁止删除 .git
禁止访问系统敏感路径
禁止执行 rm -rf /
禁止执行 sudo
禁止读取 .env
```

------

# 阶段 5：接入真实 Agent

目标：让 Mock Agent 逐步替换成真实 Agent。

## 5.1 保留 Mock Agent

Mock Agent 不要删，它适合测试系统流程。

## 5.2 Qwen Agent 优化

当前 Qwen 更像普通聊天 Agent。后续可以拆成：

```
QwenChatAgent
QwenPlannerAgent
QwenReviewerAgent
```

## 5.3 接入 OpenAI Agent

新增：

```
app/agents/openai_adapter.py
```

用途：

```
任务拆解
代码解释
Review
测试建议
```

## 5.4 接入 Claude Code / Codex

这一步放后面，不要一开始就做。

优先顺序：

```
Mock
→ Qwen
→ OpenAI
→ Claude Code
→ Codex
```

------

# 阶段 6：Orchestrator 智能化

目标：从固定拆分 backend/frontend/reviewer，升级为 LLM 动态拆解。

## 6.1 当前逻辑

现在可以先保留：

```
backend
frontend
reviewer
```

## 6.2 新逻辑

Orchestrator 输出 JSON：

```
{
  "goal": "实现登录功能",
  "tasks": [
    {
      "agent": "backend",
      "instruction": "实现登录接口",
      "depends_on": []
    },
    {
      "agent": "frontend",
      "instruction": "实现登录页面",
      "depends_on": ["backend"]
    },
    {
      "agent": "reviewer",
      "instruction": "审查整体改动",
      "depends_on": ["backend", "frontend"]
    }
  ]
}
```

## 6.3 数据库增强

Task 表建议增加：

```
task_type
priority
depends_on
started_at
finished_at
retry_count
metadata
```

## 6.4 执行策略

先做简单版本：

```
顺序执行
```

再做增强版本：

```
无依赖任务并行执行
依赖任务等待上游完成
失败任务可重试
```

------

# 阶段 7：真实 GitHub PR

目标：把当前模拟 PR 升级成真实 GitHub PR。

## 7.1 Repository 表增强

增加：

```
provider
owner
repo_name
remote_url
github_installation_id
access_token_id
```

## 7.2 PR 流程

```
Agent 修改代码
→ git diff
→ 用户确认
→ git commit
→ git push origin agent-task-{task_id}
→ GitHub API 创建 Pull Request
→ 保存 pr_url、pr_number、status
```

## 7.3 PR 表增强

```
pr_number
html_url
state
merged
base_branch
head_branch
```

## 7.4 Review Agent

让 Reviewer Agent 基于 Diff 输出：

```
风险点
潜在 Bug
安全问题
测试建议
是否建议合并
```

------

# 阶段 8：真实预览部署

目标：从本地 HTML 预览升级为真实项目预览。

## 8.1 第一版：本地 Docker 预览

流程：

```
进入 workspace
→ npm install / pnpm install
→ npm run build
→ nginx 托管 dist
→ 返回 preview_url
```

## 8.2 第二版：Vercel / Cloudflare Pages

流程：

```
推送分支
→ 调用部署平台 API
→ 获取 preview URL
→ 保存 deployment
→ WebSocket 推送
```

## 8.3 Deployment 表增强

```
external_id
preview_url
build_logs
deploy_logs
started_at
finished_at
```

------

# 阶段 9：前端体验优化

目标：让系统看起来像真正的 IM 多 Agent 协作平台。

## 9.1 页面布局

```
左侧：会话列表
中间：聊天窗口
右侧：任务 / Diff / PR / 部署面板
```

## 9.2 消息类型

支持：

```
普通文本消息
Agent 消息
任务卡片
Diff 卡片
PR 卡片
部署卡片
错误消息
日志消息
```

## 9.3 任务面板

展示：

```
父任务
子任务
Agent 名称
任务状态
执行耗时
失败原因
重试按钮
```

## 9.4 Diff 面板

支持：

```
文件列表
新增/删除/修改标记
代码高亮
接受变更
拒绝变更
创建 PR
```

------

# 阶段 10：权限、安全和稳定性

目标：让系统从演示项目变成更可靠的工程项目。

## 10.1 权限控制

需要校验：

```
用户只能访问自己的会话
用户只能访问自己的任务
用户只能访问自己的仓库
用户只能操作自己的 CodeChange / PR / Deployment
```

你目前已经做了一部分，后续可以抽成统一依赖函数，避免重复写。

## 10.2 日志系统

新增：

```
app/core/logging.py
```

记录：

```
请求日志
任务执行日志
Agent 调用日志
Git 操作日志
部署日志
异常堆栈
```

## 10.3 错误处理

统一错误格式：

```
{
  "code": "TASK_NOT_FOUND",
  "message": "任务不存在",
  "detail": {}
}
```

## 10.4 限流

需要限制：

```
登录失败次数
用户发送消息频率
Agent 任务并发数
单个任务最大运行时间
```

------

# 阶段 11：引入 LangGraph

目标：把 Orchestrator 从普通 service 升级成真正的 Agent 工作流。

建议最后再做 LangGraph，不要太早做。

## 11.1 适合 LangGraph 的地方

```
任务拆解
Agent 选择
子任务执行
失败重试
人工确认
结果汇总
```

## 11.2 工作流图

```
analyze_requirement
→ plan_tasks
→ assign_agents
→ execute_tasks
→ review_result
→ wait_user_confirm
→ generate_diff
→ create_pr
→ deploy_preview
```

## 11.3 Human-in-the-loop

加入用户确认点：

```
确认任务计划
确认代码变更
确认创建 PR
确认部署
```

------

# 推荐实现顺序

你可以按这个顺序逐步推进：

```
1. 整理当前版本：README、枚举、测试
2. 引入 Celery + Redis
3. 改造 WebSocket 事件推送
4. 拆出 workspace_service
5. 让 Agent 真实修改代码
6. 让 Diff 来自真实代码变更
7. Orchestrator 改成 LLM JSON 拆解
8. 接入真实 GitHub PR
9. 接入真实预览部署
10. 优化前端 IM 体验
11. 加强权限、安全、日志、限流
12. 最后引入 LangGraph
```