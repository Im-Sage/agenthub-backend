

> 项目需求：AgentHub - 多 Agent 协作平台 在大模型与 AI Agent 技术快速发展的背景下，多 Agent 协作已成为提升复杂任务执行效率的关键趋势。本课题聚焦 AI 驱动的开发与协作场景，要求学生基于统一适配器层与主流 Agent 平台（如 Claude Code、Codex），打造一个 IM 聊天式的多 Agent 协作平台（AgentHub）。 AgentHub 系统致力于实现类似飞书/微信的自然交互体验，支持单聊、多会话并行以及通过 @ 指令实现的群聊协作；同时集成 Orchestrator 协调器进行任务拆解，并提供代码 Diff、网页预览及一键部署等全流程功能。课题不仅考察系统的功能完整度与用户体验，更强调Prompt 工程及架构选型中的创新思考与实践。

# 一、Python 技术栈总览

## 推荐技术选型

| 模块             | 推荐技术                                   |
| ---------------- | ------------------------------------------ |
| 后端 API         | FastAPI                                    |
| 实时聊天         | FastAPI WebSocket                          |
| 数据库           | MySQL                                      |
| ORM              | SQLAlchemy 2.0                             |
| 数据迁移         | Alembic                                    |
| 缓存             | Redis                                      |
| 任务队列         | Celery                                     |
| 消息中间件       | Redis / RabbitMQ                           |
| Agent 编排       | LangGraph                                  |
| LLM 接入         | OpenAI SDK / Anthropic SDK                 |
| Claude Code 接入 | Claude Agent SDK                           |
| Codex 接入       | Codex Cloud / Codex CLI                    |
| 代码仓库操作     | GitPython / GitHub API                     |
| Diff 生成        | git diff / difflib                         |
| Web 预览         | Docker + Nginx / Cloudflare Pages / Vercel |
| 文件存储         | MinIO                                      |
| 前端             | React / Next.js / Vue 都可以               |

FastAPI 官方支持 WebSocket，适合做 IM 聊天式实时交互；Celery 是 Python 生态里常用的分布式任务队列，适合处理 Agent 执行这种耗时任务；LangGraph 官方定位就是面向 Agent 编排，支持 durable execution、streaming、human-in-the-loop 等能力。

------

# 二、整体架构

建议设计成下面这种结构：

```
前端 Web
  |
  | HTTP / WebSocket
  v
FastAPI Backend
  |
  | 创建任务 / 查询状态 / 推送消息
  v
Redis / RabbitMQ
  |
  v
Celery Worker
  |
  | 调用 Agent Adapter
  v
Claude Code / Codex / OpenAI / 自定义 Agent
  |
  v
Git Workspace / Diff / Test / Preview Deploy
```

核心思路：

```
FastAPI 负责聊天和接口
Celery 负责长任务执行
LangGraph 负责任务编排
Agent Adapter 负责统一接入不同 Agent
Git Workspace 负责代码修改和 Diff
```

不要让 FastAPI 直接执行 Agent 任务。Agent 运行时间可能很长，应该丢给 Celery Worker。FastAPI 自带 BackgroundTasks 更适合请求返回后的轻量任务，而 Celery 更适合分布式、可扩展的后台任务。

------

# 三、后端项目结构

建议这样组织项目：

```
agenthub-backend/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   └── websocket_manager.py
│   ├── api/
│   │   ├── auth.py
│   │   ├── conversations.py
│   │   ├── messages.py
│   │   ├── agents.py
│   │   ├── tasks.py
│   │   ├── repos.py
│   │   └── deployments.py
│   ├── models/
│   │   ├── user.py
│   │   ├── conversation.py
│   │   ├── message.py
│   │   ├── agent.py
│   │   ├── task.py
│   │   ├── code_change.py
│   │   └── deployment.py
│   ├── schemas/
│   │   ├── user.py
│   │   ├── message.py
│   │   ├── agent.py
│   │   └── task.py
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── chat_service.py
│   │   ├── agent_service.py
│   │   ├── orchestrator_service.py
│   │   ├── repo_service.py
│   │   └── diff_service.py
│   ├── agents/
│   │   ├── base.py
│   │   ├── orchestrator.py
│   │   ├── frontend_agent.py
│   │   ├── backend_agent.py
│   │   ├── reviewer_agent.py
│   │   ├── claude_code_adapter.py
│   │   ├── codex_adapter.py
│   │   └── mock_adapter.py
│   ├── workers/
│   │   ├── celery_app.py
│   │   └── tasks.py
│   └── db/
│       ├── session.py
│       └── base.py
├── alembic/
├── requirements.txt
└── docker-compose.yml
```

------

# 四、核心模块设计

## 1. FastAPI 后端

FastAPI 负责：

```
用户登录
会话管理
消息管理
WebSocket 推送
Agent 配置
任务创建
任务状态查询
Diff 查询
部署状态查询
```

主要接口：

```
POST   /api/auth/login
POST   /api/conversations
GET    /api/conversations
GET    /api/conversations/{id}/messages
POST   /api/messages
GET    /api/agents
POST   /api/agents
POST   /api/tasks
GET    /api/tasks/{id}
GET    /api/code-changes/{task_id}
POST   /api/deployments
```

WebSocket：

```
/ws/conversations/{conversation_id}
```

用途：

```
实时显示用户消息
实时显示 Agent 回复
实时显示任务状态变化
实时显示日志流
```

------

## 2. Celery Worker

Celery 负责处理耗时任务：

```
执行 Agent
拉取 Git 仓库
生成代码修改
运行测试
生成 Diff
创建 PR
部署预览环境
```

任务示例：

```
@celery_app.task
def run_agent_task(task_id: int):
    """
    1. 查询任务信息
    2. 加载 Agent 配置
    3. 调用 Agent Adapter
    4. 保存执行结果
    5. 推送 WebSocket 消息
    """
```

任务状态建议设计为：

```
PENDING
RUNNING
WAITING_USER_CONFIRM
SUCCESS
FAILED
CANCELLED
```

------

## 3. LangGraph Orchestrator

LangGraph 可以用来实现 Orchestrator，因为它适合做带状态的 Agent 流程编排。官方文档提到 LangGraph 重点支持 durable execution、streaming、human-in-the-loop 等能力，这些正好适合你的 AgentHub。

Orchestrator 可以设计成一个图：

```
用户需求
  |
  v
需求分析节点
  |
  v
任务拆解节点
  |
  v
Agent 选择节点
  |
  v
任务执行节点
  |
  v
结果汇总节点
  |
  v
用户确认节点
```

例如：

```
@orchestrator 帮我给博客项目增加评论功能
```

拆解结果：

```
{
  "goal": "增加评论功能",
  "tasks": [
    {
      "agent": "backend",
      "instruction": "设计评论表，实现评论新增、查询、删除接口"
    },
    {
      "agent": "frontend",
      "instruction": "实现评论列表、评论输入框、删除按钮"
    },
    {
      "agent": "reviewer",
      "instruction": "检查代码质量、安全性和边界情况"
    }
  ]
}
```

------

# 五、Agent Adapter 设计

这是项目最重要的架构点。

不要把 Claude Code、Codex、OpenAI、Mock Agent 写死在业务逻辑里。应该统一抽象为：

```
from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import List, Optional


class AgentRunRequest(BaseModel):
    task_id: int
    conversation_id: int
    repo_path: Optional[str] = None
    instruction: str
    context: dict = {}


class AgentRunResult(BaseModel):
    status: str
    summary: str
    changed_files: List[str] = []
    diff: Optional[str] = None
    logs: Optional[str] = None


class AgentAdapter(ABC):
    @abstractmethod
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        pass
```

然后实现不同 Adapter：

```
MockAgentAdapter
OpenAIAgentAdapter
ClaudeCodeAdapter
CodexAdapter
```

这样你的系统以后可以自由切换 Agent，而不用重写业务流程。

------

## Claude Code Adapter

Claude Code 现在有 Python Agent SDK，官方文档说明它可以让你用 Python 调用 Claude Code 的 Agent loop、上下文管理和工具能力，并支持读文件、运行命令、编辑代码等能力。

设计方式：

```
class ClaudeCodeAdapter AgentAdapter:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        # 1. 进入 repo_path
        # 2. 调用 Claude Agent SDK
        # 3. 收集输出
        # 4. git diff
        # 5. 返回 AgentRunResult
        pass
```

适合用于：

```
代码理解
代码修改
Bug 修复
运行测试
生成说明
```

------

## Codex Adapter

OpenAI 官方文档介绍 Codex 是 coding agent，可以读代码、编辑代码、运行代码，并且 Codex Cloud 可以在自己的云环境中处理任务，包括并行任务。

设计方式：

```
class CodexAdapter AgentAdapter:
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        # 1. 创建 Codex 任务
        # 2. 传入仓库和任务说明
        # 3. 等待执行结果
        # 4. 拉取 Diff 或 PR 信息
        # 5. 返回统一结果
        pass
```

适合用于：

```
复杂代码任务
PR 级别修改
Bug 修复
测试补充
```

------

# 六、数据库设计

Python 版仍然建议保留这些表。

## users

```
id
username
email
password_hash
created_at
```

## conversations

```
id
user_id
title
type              // single / group
created_at
updated_at
```

## messages

```
id
conversation_id
sender_type       // user / agent / system
sender_id
content
message_type      // text / task / diff / deploy
created_at
```

## agents

```
id
name
code              // orchestrator / frontend / backend / reviewer
adapter_type      // mock / openai / claude_code / codex
system_prompt
capabilities
enabled
created_at
```

## tasks

```
id
conversation_id
parent_task_id
agent_id
status
instruction
result_summary
error_message
created_at
updated_at
```

## code_changes

```
id
task_id
repo_url
branch_name
commit_hash
changed_files
diff_text
status            // generated / accepted / rejected / committed
created_at
```

## deployments

```
id
task_id
provider
preview_url
status
logs
created_at
```

------

# 七、开发路线建议

## 第 1 阶段：Python 后端基础

先完成：

```
FastAPI 项目初始化
用户登录注册
JWT 鉴权
SQLAlchemy 模型
Alembic 数据迁移
会话 CRUD
消息 CRUD
```

目标：

```
前端可以创建会话、发送消息、查看历史消息。
```

------

## 第 2 阶段：IM 实时聊天

完成：

```
FastAPI WebSocket
连接管理器
消息广播
Agent 消息推送
任务状态推送
```

演示效果：

```
用户发送消息后，聊天窗口实时出现消息。
系统任务状态可以实时更新。
```

------

## 第 3 阶段：Mock Agent

先不要急着接 Claude Code 或 Codex。

先做 Mock Agent：

```
class MockAgentAdapter(AgentAdapter):
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(
            status="success",
            summary=f"Mock Agent 已处理任务：{request.instruction}",
            changed_files=[],
            diff=None,
            logs="mock logs"
        )
```

这样可以先跑通：

```
@ 指令
任务创建
Celery 执行
Agent 回复
WebSocket 推送
```

------

## 第 4 阶段：Orchestrator

实现：

```
@orchestrator 指令识别
任务拆解 Prompt
JSON 结构化输出
任务依赖关系
多个 Agent 子任务创建
```

示例：

```
用户输入：
@orchestrator 给项目增加登录功能

系统拆解：
1. backend-agent 实现登录接口
2. frontend-agent 实现登录页面
3. reviewer-agent 审查代码
```

------

## 第 5 阶段：Git 仓库与 Diff

实现：

```
绑定 GitHub 仓库
拉取仓库到本地 workspace
创建临时分支
执行 Agent 修改
生成 git diff
保存 diff_text
前端展示 Diff
```

Python 可以用：

```
GitPython
subprocess 调用 git
difflib
```

实际建议优先用 `subprocess` 调 git 命令，因为更贴近真实开发环境：

```
git clone
git checkout -b agent-task-xxx
git diff
git add
git commit
```

------

## 第 6 阶段：真实 Agent 接入

接入顺序建议：

```
1. OpenAI 普通 Agent / Chat Completions
2. Claude Code Agent SDK
3. Codex
```

不要一开始就接 Codex 或 Claude Code。先用普通 LLM 把流程跑通。

------

## 第 7 阶段：PR 和预览部署

增强功能：

```
创建 Pull Request
展示 PR 链接
运行测试
构建前端项目
生成预览部署
展示预览 URL
```

这部分可以放到最后，不影响核心答辩。

------

# 八、MVP 版本范围

如果你时间有限，Python 版 MVP 建议只做这些：

```
1. FastAPI 后端
2. 用户登录
3. 会话和消息
4. WebSocket 实时聊天
5. Agent 配置
6. @agent 指令
7. Celery 异步执行 Agent
8. Mock Agent
9. Orchestrator 任务拆解
10. Diff 展示
```

也就是说，MVP 不一定要真的完成一键部署。你可以把部署作为扩展功能。

------

# 九、最终推荐技术组合

我建议你最终采用这套：

```
后端：FastAPI
数据库：MySQL
ORM：SQLAlchemy 2.0
迁移：Alembic
缓存：Redis
任务队列：Celery + Redis
Agent 编排：LangGraph
Agent 接入：OpenAI SDK + Claude Agent SDK
代码仓库：Git + GitPython / subprocess
文件存储：MinIO
前端：React + Tailwind CSS
部署：Docker Compose
```

最关键的后端组合是：

```
FastAPI + WebSocket + Celery + Redis + LangGraph
```

这个组合既符合 Python 技术栈，又能很好支撑你的项目核心卖点：

```
IM 聊天
多 Agent 协作
任务拆解
异步执行
状态实时推送
代码 Diff
人工确认
```