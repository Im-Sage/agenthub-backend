# AgentHub LangGraph Durable Human-in-the-loop Implementation Plan

> **For Codex / agentic workers:** Execute this plan task-by-task. Prefer TDD and small commits. Do not batch-edit the whole repository in one pass. At every `HUMAN GATE`, stop, explain the current state transition, show the relevant diff, and let the developer complete or review the marked code before continuing.

**Goal:** Replace AgentHub's current “Planner 生成计划后结束 Graph，用户确认后重新启动一次 Orchestrator Graph”机制，改造成基于 LangGraph Checkpointer + `interrupt()` + `Command(resume=...)` 的真正持久化暂停与恢复。

**Architecture:** 每个业务 Orchestrator Task 使用稳定且唯一的 `thread_id = "orchestrator-task-{task_id}"`。首次 Celery 任务运行 Graph，Planner 生成计划后进入独立 Approval Node，并在 `interrupt()` 处暂停；Checkpointer 持久化 AgentState 和 Graph 执行位置。用户确认后 FastAPI 不再重新启动新 Graph，而是提交 Resume Celery Task，使用相同 `thread_id` 和 `Command(resume=...)` 恢复原工作流。

**Tech Stack:** FastAPI, Celery, Redis, SQLAlchemy, LangGraph, `langgraph-checkpoint-sqlite`, pytest.

## Global Constraints

1. 保留现有 `Task` 表作为业务状态的唯一展示来源；LangGraph Checkpointer 只负责工作流运行时状态，不替代业务 Task。
2. 保留现有 WebSocket / Redis Pub/Sub 事件模型，前端仍通过 `task.created`、`task.updated` 获取任务状态。
3. 本次只实现“确认后继续执行”；不扩展计划编辑、拒绝重规划、时间旅行 UI。
4. 本地和当前项目环境使用持久化 SQLite Checkpointer；不要在本次改造中引入 PostgreSQL。
5. `thread_id` 必须由业务 `task_id` 确定性生成，不使用随机 UUID，不使用 `conversation_id`。
6. `interrupt()` 必须放在独立 Approval Node 中；不要把它直接放进当前具有“调用 LLM、创建子任务、写数据库、广播事件”等副作用的 `plan_node`。
7. 不允许在 `interrupt()` 前执行非幂等副作用。
8. 用户点击确认后必须通过 `Command(resume=...)` 恢复，不得再次向 Graph 传入新的 `initial_state`。
9. 确认接口不得再次调用 `run_orchestrator_task.delay(...)`；应调用新的 `resume_orchestrator_task.delay(...)`。
10. 保持现有普通 Qwen Agent、Mock Agent、非 Orchestrator Task 行为不变。
11. 每个任务完成后运行相关测试并提交一个独立 Git commit。

---

## Current Behavior to Replace

当前关键链路：

```text
POST /messages
  -> 创建 Parent Task
  -> Celery run_orchestrator_task
  -> LangGraph planner
  -> 生成 plan + child tasks
  -> awaiting_confirmation=True
  -> Graph END
  -> 用户 POST /tasks/{id}/plan/confirm
  -> 再次 run_orchestrator_task.delay(task.id)
  -> 新的一次 Graph 从 START 开始
  -> plan_node 从数据库读取 confirmed plan
  -> executor...
```

目标链路：

```text
POST /messages
  -> 创建 Parent Task
  -> Celery run_orchestrator_task
  -> Graph(thread_id=orchestrator-task-{id})
  -> planner
  -> approval
  -> interrupt()
  -> Checkpointer 持久化并暂停

用户确认
  -> POST /tasks/{id}/plan/confirm
  -> Celery resume_orchestrator_task
  -> Graph + 同一 thread_id
  -> Command(resume={"approved": true})
  -> approval 节点恢复
  -> executor
  -> verifier
  -> summarizer
  -> SUCCESS
```

---

## File Map

### Modify

- `requirements.txt`
  - 显式加入 LangGraph 及 SQLite checkpointer 依赖。

- `app/core/config.py`
  - 增加 Checkpoint SQLite 文件路径配置和项目根目录解析。

- `app/agents/graph/state.py`
  - 增加审批状态字段。

- `app/agents/graph/nodes.py`
  - 简化 `plan_node` 的“二次启动恢复”逻辑。
  - 新增 `approval_node`。

- `app/agents/graph/workflow.py`
  - 插入 Approval Node。
  - 删除现有 `planner_router -> END` 的伪暂停逻辑。
  - 支持传入 checkpointer 编译 Graph。

- `app/agents/langgraph_adapter.py`
  - 首次执行带固定 `thread_id`。
  - 新增 resume 方法，通过 `Command(resume=...)` 恢复。
  - 正确识别 interrupt 状态。

- `app/workers/agent_tasks.py`
  - 修正首次 Orchestrator Worker 对“等待确认”的状态处理。
  - 新增 `resume_orchestrator_task` Celery Task。

- `app/api/tasks.py`
  - Confirm API 改为提交 Resume Celery Task，而不是重新启动 Orchestrator。

### Create

- `app/agents/graph/runtime.py`
  - 统一管理 `thread_id`、Graph config、Async SQLite checkpointer 生命周期。

- `tests/test_langgraph_interrupt_resume.py`
  - 测试 interrupt、同 thread 恢复、不同 thread 隔离。

- `tests/test_langgraph_sqlite_persistence.py`
  - 测试关闭并重新打开 SQLite checkpointer 后仍能恢复。

- `tests/test_task_plan_resume.py`
  - 测试确认接口提交 resume task，而不是 initial run task。

---

# Task 1: Add Explicit LangGraph Persistence Dependencies

**Files:**
- Modify: `requirements.txt`

在现有依赖中补充：

```text
langgraph
langchain-openai
langgraph-checkpoint-sqlite
```

不要删除现有依赖，不要顺手升级 FastAPI、SQLAlchemy、Celery 等无关包。

验证：

```bash
pip install -r requirements.txt
python -c "from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver; from langgraph.types import Command, interrupt; print('ok')"
```

期望输出：

```text
ok
```

提交：

```bash
git add requirements.txt
git commit -m "chore: add langgraph checkpoint dependencies"
```

---

# Task 2: Add Persistent Checkpoint Runtime

**Files:**
- Modify: `app/core/config.py`
- Create: `app/agents/graph/runtime.py`
- Test: `tests/test_langgraph_runtime.py`

在 `Settings` 中增加：

```python
langgraph_checkpoint_path: str = "./langgraph_checkpoints.sqlite3"
```

增加属性，将相对路径固定到 `PROJECT_ROOT`：

```python
@property
def resolved_langgraph_checkpoint_path(self) -> str:
    path = Path(self.langgraph_checkpoint_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve().as_posix()
```

创建 `app/agents/graph/runtime.py`，提供以下稳定接口：

```python
def graph_thread_id(task_id: int) -> str:
    return f"orchestrator-task-{task_id}"


def graph_config(task_id: int) -> dict:
    return {
        "configurable": {
            "thread_id": graph_thread_id(task_id),
        }
    }
```

并提供一个异步 context manager：

```python
@asynccontextmanager
async def open_agent_graph():
    ...
```

要求：

1. 使用 `AsyncSqliteSaver.from_conn_string(...)`。
2. 确保 checkpoint 文件父目录存在。
3. 在 context manager 内调用 `create_agent_graph(checkpointer=checkpointer)`。
4. 不创建全局长期持有的 SQLite 连接。
5. 首次执行和 Resume 执行必须打开同一个 checkpoint 文件。

测试至少覆盖：

```python
assert graph_thread_id(123) == "orchestrator-task-123"
assert graph_config(123)["configurable"]["thread_id"] == "orchestrator-task-123"
assert graph_thread_id(123) != graph_thread_id(124)
```

## HUMAN GATE 1

到这里停止。开发者亲自实现 `graph_thread_id()` 和 `graph_config()`，Codex 只负责：

1. 检查类型和命名；
2. 补测试；
3. 指出为什么不能使用 `conversation_id` 作为 thread_id。

提交：

```bash
git add app/core/config.py app/agents/graph/runtime.py tests/test_langgraph_runtime.py
git commit -m "feat: add persistent langgraph runtime"
```

---

# Task 3: Introduce a Dedicated Approval Interrupt Node

**Files:**
- Modify: `app/agents/graph/state.py`
- Modify: `app/agents/graph/nodes.py`

在 `AgentState` 增加：

```python
approval_status: str | None
```

保留现有 `awaiting_confirmation` 字段作为业务/UI 状态镜像。

新增：

```python
from langgraph.types import interrupt


async def approval_node(state: AgentState) -> Dict[str, Any]:
    decision = interrupt(
        {
            "type": "orchestrator_plan_approval",
            "task_id": state["task_id"],
            "plan": state["plan"],
        }
    )

    approved = (
        bool(decision.get("approved"))
        if isinstance(decision, dict)
        else bool(decision)
    )

    return {
        "approval_status": "approved" if approved else "rejected",
        "awaiting_confirmation": False,
        "is_finished": not approved,
    }
```

关键要求：

- `interrupt()` 之前不能写数据库、不能创建 Task、不能调用模型、不能广播事件。
- Approval Node 只负责暂停和消费恢复值。

同时调整 `plan_node`：

1. 删除或废弃“检测数据库 `plan_status == confirmed` 后直接返回旧 plan”的二次启动恢复分支，因为真正恢复将通过 checkpoint 完成。
2. 生成 plan 后仍然创建 child tasks、保存 plan metadata、广播状态。
3. `plan_status` 设置为 `awaiting_confirmation`。
4. Parent Task 保持 `PENDING`。
5. `finished_at` 必须为 `None`，因为等待人工确认不代表任务结束。
6. 返回状态应为：

```python
{
    "plan": plan,
    "current_step_index": 0,
    "current_agent": plan[0]["agent"] if plan else None,
    "current_instruction": plan[0]["instruction"] if plan else None,
    "metadata_json": json.dumps({"child_ids": child_ids}),
    "awaiting_confirmation": True,
    "approval_status": None,
    "errors": [],
    "is_finished": not bool(plan),
    "final_summary": None,
}
```

## HUMAN GATE 2

开发者亲自写 `approval_node()`，不要让 Codex 直接生成最终实现。写完后让 Codex review 以下问题：

1. `interrupt()` 前有没有副作用？
2. Resume 后节点为什么会从函数开头重新执行？
3. Resume value 最终如何成为 `interrupt()` 的返回值？
4. `is_finished` 在 approved / rejected 时是否正确？

提交：

```bash
git add app/agents/graph/state.py app/agents/graph/nodes.py
git commit -m "feat: add langgraph approval interrupt"
```

---

# Task 4: Rewire the Graph Around the Interrupt

**Files:**
- Modify: `app/agents/graph/workflow.py`

目标 Graph：

```text
START
  -> planner
  -> approval
      -> approved -> executor
      -> rejected -> END
  -> executor
  -> verifier
      -> executor   (继续执行/自愈)
      -> summarizer (全部完成)
  -> END
```

要求：

1. 注册 `approval_node`。
2. 删除当前 `planner_router` 的 `awaiting_confirmation -> END` 逻辑。
3. 固定边：

```python
workflow.add_edge(START, "planner")
workflow.add_edge("planner", "approval")
```

4. 新增 approval router：

```python
def approval_router(state: AgentState):
    if state.get("approval_status") == "approved":
        return "executor"
    return END
```

5. `create_agent_graph` 改为接收可选 checkpointer：

```python
def create_agent_graph(checkpointer=None):
    ...
    return workflow.compile(checkpointer=checkpointer)
```

6. 移除全局无持久化 `agent_graph = create_agent_graph()`，并更新所有引用者，统一通过 `open_agent_graph()` 获取已绑定 checkpointer 的 compiled graph。

测试必须证明：

- 首次 invoke 到达 Approval interrupt 时 Executor 尚未执行。
- 使用同一 `thread_id` + `Command(resume={"approved": True})` 后 Executor 开始执行。
- 使用不同 `thread_id` 不能恢复另一个 Task 的状态。

提交：

```bash
git add app/agents/graph/workflow.py tests/test_langgraph_interrupt_resume.py
git commit -m "feat: persist and resume orchestrator graph"
```

---

# Task 5: Make LangGraphOrchestratorAdapter Support Start and Resume

**Files:**
- Modify: `app/agents/langgraph_adapter.py`

重构目标：

```text
run(request)
  -> initial_state
  -> open_agent_graph()
  -> graph.ainvoke(initial_state, config=graph_config(task_id))

resume(task_id, resume_value)
  -> open_agent_graph()
  -> graph.ainvoke(Command(resume=resume_value), config=graph_config(task_id))
```

新增：

```python
from langgraph.types import Command
```

`run()` 必须使用固定 config：

```python
config = graph_config(request.task_id)
```

新增方法：

```python
async def resume(
    self,
    task_id: int,
    resume_value: dict,
) -> AgentRunResult:
    ...
```

不要修改通用 `AgentAdapter` 抽象基类，因为 Resume 是当前 Orchestrator 特有能力，不要求 Qwen/Mock Adapter 实现。

实现一个私有结果转换函数，统一把 Graph 输出转换成 `AgentRunResult`。

首次运行遇到 interrupt 时返回：

```python
AgentRunResult(
    status="awaiting_confirmation",
    summary="Orchestrator plan generated and is awaiting confirmation.",
    changed_files=[],
    logs="LangGraph interrupted for human approval.",
)
```

正常执行到 summarizer 后返回 `status="success"`。

要求：

- 不要通过检查数据库 `plan_status` 决定是否 Resume。
- Resume 的唯一机制是同一 `thread_id` + `Command(resume=...)`。
- 不要给 Resume 再传 `initial_state`。

提交：

```bash
git add app/agents/langgraph_adapter.py
git commit -m "feat: support langgraph command resume"
```

---

# Task 6: Add a Dedicated Resume Celery Task

**Files:**
- Modify: `app/workers/agent_tasks.py`

保留：

```python
run_orchestrator_task(parent_task_id: int)
```

它只负责首次启动工作流。

新增：

```python
@celery_app.task(name="app.workers.agent_tasks.resume_orchestrator_task")
def resume_orchestrator_task(parent_task_id: int, resume_value: dict):
    ...
```

Resume Task 行为：

1. 查询 Parent Task。
2. 将业务 Task 更新为 `RUNNING`，`finished_at=None`，广播 `task.updated`。
3. 创建 `LangGraphOrchestratorAdapter()`。
4. 调用：

```python
adapter.resume(
    parent_task_id,
    resume_value,
)
```

5. Graph 完整执行成功后，由现有 summarizer 将 Parent Task 更新为 `SUCCESS`。
6. 发生异常时更新 `FAILED`、`error_message`、`finished_at` 并广播。

同时修正首次 `run_orchestrator_task`：

- 如果 `run_result.status == "awaiting_confirmation"`：
  - Parent Task 保持 `PENDING`；
  - `finished_at = None`；
  - 不要标记 SUCCESS；
  - 直接返回等待确认结果。

- 如果 Graph 完整成功：
  - 不要覆盖 summarizer 已经写入的 `SUCCESS` 状态。

## HUMAN GATE 3

开发者先自己画出以下状态机，再让 Codex 检查实现：

```text
PENDING
  -> RUNNING (首次 Graph)
  -> PENDING / awaiting_confirmation
  -> RUNNING (Resume Celery Task)
  -> SUCCESS

任何执行阶段异常
  -> FAILED
```

开发者必须亲自解释：为什么“等待人工确认”不能设置 `finished_at`。

提交：

```bash
git add app/workers/agent_tasks.py
git commit -m "feat: resume interrupted orchestrator tasks"
```

---

# Task 7: Change the Confirm API to Resume Instead of Restart

**Files:**
- Modify: `app/api/tasks.py`
- Modify: `app/services/task_service.py` only if validation semantics require adjustment
- Test: `tests/test_task_plan_resume.py`

当前 Confirm API 中必须删除：

```python
agent_tasks.run_orchestrator_task.delay(task.id)
```

改为：

```python
agent_tasks.resume_orchestrator_task.delay(
    task.id,
    {"approved": True},
)
```

保留现有 `confirm_orchestrator_plan()` 的业务级幂等保护：只有 `plan_status == awaiting_confirmation` 时允许确认。第一次确认后将 metadata 中 `plan_status` 更新为 `confirmed`，这样用户双击 Confirm 时第二次请求会被拒绝，不会重复提交 Resume。

Confirm API 返回前：

1. 保存新的 Celery task id。
2. 广播一次 `task.updated`。
3. 返回当前 Task。

测试要求：

```text
Given: plan_status = awaiting_confirmation
When: POST /tasks/{id}/plan/confirm
Then:
  resume_orchestrator_task.delay 被调用一次
  run_orchestrator_task.delay 没有被调用
  resume payload == {"approved": True}
```

再测试重复确认：

```text
第一次 confirm 成功
第二次 confirm 返回 400
不会再次提交 resume task
```

## HUMAN GATE 4

开发者亲自修改 Confirm API 中“提交哪个 Celery Task”的 3-5 行核心代码，Codex 负责补 mock 测试和 review。

提交：

```bash
git add app/api/tasks.py app/services/task_service.py tests/test_task_plan_resume.py
git commit -m "feat: resume graph on plan confirmation"
```

---

# Task 8: Prove Persistence Across Checkpointer Reopen

**Files:**
- Create: `tests/test_langgraph_sqlite_persistence.py`

这个测试必须证明的不是“同一个 Python 对象可以 resume”，而是：

```text
打开 Checkpointer A
  -> 运行到 interrupt
  -> 关闭 A

重新打开 Checkpointer B，使用同一个 SQLite 文件
  -> 相同 thread_id
  -> Command(resume=...)
  -> 成功从原 checkpoint 继续
```

测试中不要调用真实 Qwen API。通过 monkeypatch `workflow.py` 中的节点引用，使用 fake planner / executor / verifier / summarizer，仅保留真实 `approval_node`。

核心断言：

1. 第一次执行后存在 interrupt。
2. 第一次执行后 executor 未调用。
3. 关闭第一个 AsyncSqliteSaver。
4. 新建第二个 AsyncSqliteSaver 指向相同临时 SQLite 文件。
5. 用相同 `thread_id` Resume 后 executor 被调用。
6. 最终状态包含预期 `final_summary`。

提交：

```bash
git add tests/test_langgraph_sqlite_persistence.py
git commit -m "test: verify durable langgraph resume"
```

---

# Task 9: Manual End-to-End Acceptance Test

不要跳过手工验证。

执行：

```text
1. 启动 Redis、FastAPI、Celery Worker。
2. 创建 Conversation 并绑定 Repository。
3. 发送 @orchestrator 任务。
4. 确认前端收到：
   - task.created
   - task.updated
   - Parent Task plan_status=awaiting_confirmation
5. 确认此时 child task 尚未开始真正执行。
6. 停止 Celery Worker。
7. 停止并重新启动 FastAPI（可选但推荐）。
8. 重新启动 Celery Worker。
9. 调用 POST /tasks/{task_id}/plan/confirm。
10. 确认提交的是 resume_orchestrator_task。
11. 确认 Graph 从 Approval 后继续，不重新调用 Planner，不重复创建 child tasks。
12. 确认 Task 最终到达 SUCCESS。
13. 确认 WebSocket 状态流正常。
```

必须额外检查数据库：

```text
同一个 Parent Task 只存在一组 child tasks。
确认恢复后没有重复 plan。
确认恢复后没有重复 child task。
```

---

# Acceptance Criteria

全部满足才算完成：

- [ ] Planner 只执行一次。
- [ ] Child Tasks 只创建一次。
- [ ] Graph 在 `interrupt()` 处真正暂停。
- [ ] 暂停时 Celery Worker 不需要持续占用执行槽等待用户。
- [ ] Checkpoint 文件关闭并重新打开后仍可恢复。
- [ ] Resume 使用相同 `thread_id`。
- [ ] Resume 使用 `Command(resume=...)`。
- [ ] Confirm API 不再调用 `run_orchestrator_task.delay(...)`。
- [ ] 等待确认时 Parent Task 不设置 `finished_at`。
- [ ] 原有 Qwen / Mock 普通任务测试继续通过。
- [ ] WebSocket `task.updated` 行为没有回归。
- [ ] 重复点击 Confirm 不会重复 Resume。
- [ ] 不调用真实 LLM 的自动化测试可以稳定复现 interrupt/resume。

---

# Commands to Run Before Declaring Completion

至少运行：

```bash
pytest tests/test_langgraph_runtime.py -v
pytest tests/test_langgraph_interrupt_resume.py -v
pytest tests/test_langgraph_sqlite_persistence.py -v
pytest tests/test_task_plan_resume.py -v
pytest -q
```

然后检查：

```bash
git diff --check
git status
```

不要在测试失败的情况下声称完成。

---

# Required Codex Working Style

1. 每次最多完成一个 Task，不要一次改完全部文件。
2. 每个 Task 开始前先说明：当前旧行为、这一步要改变的状态流、会改哪些文件。
3. 每个 HUMAN GATE 必须停止，让开发者自己写或解释核心代码。
4. 开发者写完后，Codex 先 review，不要直接重写。
5. 每一步先写/补测试，再改实现。
6. 不做与本功能无关的“大规模重构”。
7. 如果发现当前代码中的其他 Bug，只记录到单独的 `Follow-up` 列表，不在本次任务顺手修复，除非它直接阻塞 interrupt/resume。
8. 每个 commit 后给出一句话说明：该 commit 建立了哪一条新的系统不变量。

---

# Expected Final Design Explanation

实现完成后，Codex 必须让开发者能够独立解释以下内容：

```text
Task 表：业务状态
Checkpointer：Graph 运行时状态
thread_id：定位同一个持久化工作流
interrupt()：创建持久化暂停点
Command(resume=...)：向暂停点注入人工输入并继续
Celery：首次启动和恢复执行的异步运行载体
Redis Broker：分发 Start / Resume Celery Task
WebSocket：向前端广播业务 Task 状态
```

最终一句话架构描述应为：

> AgentHub 将每个 Orchestrator Task 映射为一个稳定的 LangGraph thread。Planner 生成计划后，Graph 在独立 Approval Node 中通过 interrupt 持久化暂停；用户确认后，FastAPI 提交 Resume Celery Task，并使用同一 thread_id 和 Command(resume) 从 checkpoint 恢复原工作流，而不是重新启动一条新的 Graph 执行链。
