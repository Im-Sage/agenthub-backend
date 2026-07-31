# LangGraph SQLite / PostgreSQL Checkpointer

## Checkpoint 与业务数据库

LangGraph checkpoint 保存工作流控制状态和 HITL 中断点；SQLAlchemy 业务数据库保存 Task、Message、CodeChange 等业务事实。Celery 执行状态同样写入业务数据库，不能只存在于 broker 或 checkpoint。切换 checkpointer 后端不会删除、绕过或替换既有 LangGraph HITL。

## SQLite 默认配置

SQLite 适合单机开发和简历演示，保持默认：

```env
LANGGRAPH_CHECKPOINT_BACKEND=sqlite
LANGGRAPH_CHECKPOINT_PATH=./langgraph_checkpoints.sqlite3
```

相对路径按项目根目录解析。应用使用 `AsyncSqliteSaver`，无需外部服务。

## PostgreSQL 配置

多进程或跨连接恢复使用 PostgreSQL：

```env
LANGGRAPH_CHECKPOINT_BACKEND=postgres
LANGGRAPH_CHECKPOINT_DATABASE_URL=postgresql://agenthub:agenthub@localhost:5433/agenthub_checkpoints?sslmode=disable
LANGGRAPH_CHECKPOINT_AUTO_SETUP=true
```

PostgreSQL 模式使用 `AsyncPostgresSaver.from_conn_string()`。缺少 DSN 时配置校验会清晰失败。`LANGGRAPH_CHECKPOINT_AUTO_SETUP=true` 时，进程内锁和完成标志保证 `setup()` 幂等；关闭后由运维迁移流程负责建立 saver 表。

## 恢复语义

调用方必须为同一工作流复用稳定的 `thread_id`。PostgreSQL 集成测试使用两个独立连接：第一个连接写入 checkpoint，关闭后第二个连接以相同 thread 读取，从而验证恢复不依赖进程内对象。SQLite 和 PostgreSQL 都沿用同一 LangGraph graph/HITL API。

真实验证需要：

```powershell
$env:AGENTHUB_TEST_POSTGRES_DSN="postgresql://agenthub:agenthub@localhost:5433/agenthub_checkpoints?sslmode=disable"
python -m pytest tests/integration/test_postgres_checkpointer_integration.py -q
```

没有可连接 PostgreSQL 时，该集成测试结果必须如实记录为 skip/blocked，不能宣称通过。
