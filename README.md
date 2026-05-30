# AgentHub Backend

AgentHub 是一个 AI 驱动的多 Agent 协作平台，旨在通过自然语言交互实现复杂软件开发任务的自动化。本项目是 AgentHub 的后端工程。

## 项目简介

AgentHub 模仿 IM 聊天体验，支持用户与多个 Agent 进行协作。核心能力包括需求拆解（Orchestrator）、代码自动修改、生成 Diff、创建模拟 PR 以及本地预览部署。

## 技术栈

- **后端框架**: FastAPI
- **数据库**: SQLite (默认) / MySQL
- **ORM**: SQLAlchemy 2.0
- **数据库迁移**: Alembic
- **实时通信**: WebSocket
- **大模型接入**: 阿里云百炼 (通义千问)
- **代码操作**: GitPython / subprocess

## 启动方式

1. **安装依赖**:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. **初始化数据库**:
   ```powershell
   .\.venv\Scripts\python.exe -m alembic upgrade head
   ```
3. **运行服务**:
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

服务启动后访问：`http://127.0.0.1:8000/docs` 查看 API 文档。

## 环境变量

在项目根目录创建 `.env` 文件：

```text
DATABASE_URL=sqlite:///./agenthub.db
SECRET_KEY=your-secret-key-here

# 阿里云百炼配置
ALIYUN_API_KEY=your-api-key
ALIYUN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIYUN_MODEL=qwen-plus
```

## 当前已完成功能

- **第一阶段**: 注册登录、JWT 鉴权、会话/消息 CRUD
- **第二阶段**: WebSocket 实时聊天和消息广播
- **第三阶段**: `@mock` 指令、任务创建、任务状态推送
- **第四阶段**: `@orchestrator` 指令、父子任务拆解、多 Agent 协作
- **第五阶段**: 绑定 Git 仓库、生成代码 Diff
- **第六阶段**: 接入通义千问真实 LLM 回复
- **第七阶段**: 创建模拟 PR、生成预览部署、推送事件

## 后续计划 (V2 规划)

1. **异步化**: 引入 Celery + Redis 处理长耗时 Agent 任务。
2. **事件推送**: 升级 WebSocket 推送机制，支持分布式架构。
3. **代码变更**: 让 Agent 真实修改代码，完善安全限制。
4. **GitHub 集成**: 支持真实的 GitHub Pull Request。
5. **预览部署**: 支持 Docker 或云平台预览部署。
6. **流程编排**: 引入 LangGraph 增强 Agent 工作流。

## 接口概览

- **Auth**: `/api/auth/register`, `/api/auth/login`
- **Conversation**: `/api/conversations`, `/api/messages`
- **Agent/Task**: `/api/agents`, `/api/tasks`
- **Repo/Code**: `/api/repos`, `/api/code-changes`
- **PR/Deploy**: `/api/pull-requests`, `/api/deployments`
- **WebSocket**: `/ws/conversations/{id}`

---
详细开发日志请参考 `agenthub-backend.md`。
