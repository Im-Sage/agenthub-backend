# Celery Orchestrator 与 Git Worktree

## 职责边界

LangGraph 负责生成和确认结构化计划，并保留既有 HITL 中断点；它不直接执行耗时子任务。计划确认后，Orchestrator 把依赖图转换为 DAG Wave，由 Celery Canvas 调度。每个 graph child 都是独立 Celery task，拥有自己的数据库状态、Celery task id、重试次数、日志、分支和 commit。

Celery Canvas 使用 immutable signatures，避免 chord/group 的上游结果被意外注入下游参数。每个 Wave 是可并行的 group，merge callback 按 `step_index` 顺序 cherry-pick；上一 Wave 成功合并后才准备下一 Wave。无论业务成功、冲突、取消还是异常，错误都会先写入数据库，finalizer 最终执行并把父任务收敛到终态。

## DAG 与 Wave

`depends_on` 定义有向无环图。调度器进行拓扑分层：

```text
Wave 0: backend, frontend
Wave 1: reviewer (depends_on backend + frontend)
```

同一 Wave 的步骤都从同一个 integration commit 建立，避免先完成的步骤改变其他并行步骤的基线。Reviewer 只有在前置 Wave 已合并后才运行，因此看到的是已集成结果。

## Worktree 和分支命名

所有临时目录位于 `AGENT_WORKTREE_ROOT`：

```text
<root>/user-<user_id>/repo-<repository_id>/orchestrator-<parent_task_id>/integration
<root>/user-<user_id>/repo-<repository_id>/orchestrator-<parent_task_id>/steps/<step_key>
```

对应分支：

```text
agent/orchestrator-<parent_task_id>/integration
agent/orchestrator-<parent_task_id>/<step_key>
```

每个 step 在独立物理目录运行、验证并提交。不能让并行 Agent 共享主 Workspace：共享 cwd 会产生未提交文件互相覆盖、Git index 竞争、错误 diff 归属和取消清理误删等竞态。仓库级 Git 元数据操作使用锁；reset/clean 仅允许针对校验过的 step worktree。

## 合并、冲突与 CodeChange

成功步骤必须产生独立 commit。merge callback 按计划顺序 cherry-pick 到 integration branch；即使两个步骤声明的 `write_scope` 不相交，Git 仍是最终冲突检测器。同一行冲突会 abort cherry-pick、记录冲突文件并保留此前 integration 内容，不用后到结果覆盖先到结果。

所有 Wave 完成后，finalizer 从 integration worktree 生成唯一 CodeChange、消息和增量索引事件。integration branch 可继续用于 Diff、Review 和 PR；step worktree/branch 在终态后清理。

## 幂等、重试、取消与恢复

- task id、execution generation 和数据库 claim 共同阻止重复投递重复执行。
- 重复 callback、merge、finalize 不会重复生成 commit、消息或 CodeChange。
- 验证失败在同一隔离 step 中有限修复；Celery transport retry 与业务修复次数分开记录。
- 取消会递增 generation、撤销已知 Celery task，并设置持久化 barrier；旧 generation callback 不能复活任务。
- 恢复会从数据库读取 Wave/step/commit 状态，只补投缺失工作；已成功和已合并部分保持幂等。
- worker 丢失、软超时和执行异常必须持久化失败；finalizer 仍负责清理和父任务终态。

相关测试：`tests/test_orchestrator_recovery.py`、`tests/test_orchestrator_atomicity.py` 和 `tests/integration/test_orchestrator_canvas_integration.py`。
