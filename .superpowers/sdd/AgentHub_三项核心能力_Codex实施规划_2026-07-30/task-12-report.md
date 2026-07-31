# Task 12 Report — Cancellation, Retry, Recovery, and Cleanup

## STATUS

COMPLETE — committed as `a5a0dda` (`feat: recover and cancel orchestrator executions`).

## Changes

- Added the recovery service for orchestrator cancellation, retry from the
  first incomplete wave, reconciliation, and terminal cleanup.
- Cancellation revokes root and child Celery task ids, persists child and
  parent cancellation state, and safely cleans eligible step worktrees.
- Retry preserves successful merged steps, resets incomplete steps, clears the
  old Canvas id, and dispatches only remaining waves. Dispatch failures are
  persisted on the parent task.
- Reconciliation aborts residual cherry-picks, recreates missing step
  worktrees from their recorded base commit, recognizes already merged commits,
  prunes worktrees, and emits recovery logs.
- Cleanup preserves integration branches for generated, accepted, and committed
  CodeChanges; force cleanup removes eligible failed/rejected integration state
  and prunes worktrees.
- Added API recovery/cleanup endpoints and routed worker cleanup through the
  recovery service. The retry test patches the actual delayed-import dependency
  boundary rather than requiring a recovery-module implementation detail.

## Tests

Command:

```powershell
python -m pytest tests/test_orchestrator_recovery.py tests/test_orchestrator_dispatch_service.py tests/test_orchestrator_execution_service.py tests/test_orchestrator_resume_task.py tests/test_worktree_service.py -q
```

Real output summary: `25 passed, 4 warnings in 20.39s`.

The warnings are pre-existing deprecations in `app/workers/agent_tasks.py`
(`datetime.utcnow()` and `asyncio.get_event_loop()`); they do not originate in
Task 12.

## Commit

`a5a0dda feat: recover and cancel orchestrator executions`

## Concerns

- The repository currently emits the four deprecation warnings above during
  LangGraph resume regression tests.
- The root Canvas is revoked best-effort; any revoke or safe-worktree cleanup
  error is now retained in the parent task's error state for operator recovery.

## Review fix round 1

STATUS: COMPLETE — `22bd9da fix: harden orchestrator recovery state transitions`.

Added generation-aware execution barriers, execution-phase-only recovery
routing, full known Canvas task-id tracking, independent revoke error capture,
skipped-wave retry preservation, integration worktree reconciliation, and
diagnostic preservation unless cleanup is forced.

Verification command:

```powershell
python -m pytest tests/test_orchestrator_recovery.py tests/test_orchestrator_dispatch_service.py tests/test_orchestrator_execution_service.py -q
```

Real output: `19 passed in 5.78s`.

Concern: the focused suite does not include the full API retry integration
fixture; the execution-phase gate is covered at service level.

## Review fix round 2

STATUS: COMPLETE — `a08b759 fix: guard stale orchestrator canvas callbacks`.

Canvas callbacks now receive an execution generation; stale prepare, wave,
merge, and finalizer callbacks return structured `skipped`. Dispatch persists
the stable execution id and all Celery ids before publish. Cancellation and
retry generation checks protect stale step completion paths.

Command: `python -m pytest tests/test_orchestrator_dispatch_service.py tests/test_orchestrator_tasks.py -q`

Real output: `7 passed in 3.45s`.

Concern: broader generation concurrency and full API suites remain to be run in
the next review cycle.

## Review fix round 3 completion

STATUS: COMPLETE — implementation `d8d135d fix: make cancellation generation barriers durable`; regression tests `0bf9315 test: cover durable orchestrator cancellation barriers`.

The exception path now force-refreshes parent and child rows with
`SELECT ... FOR UPDATE` and `populate_existing=True`; cancellation atomically
increments the execution generation and cancellation failures are persisted as
one structured JSON value containing both revocation and cleanup errors.

Commands and real outputs:

```powershell
python -m pytest tests/test_orchestrator_recovery.py -q
# 11 passed in 5.77s

$env:TEMP = 'E:\demo\agenthub-backend\.pytest-task2-review\codex-orchestration-postgres-mcp\.tmp-task12'; $env:TMP = $env:TEMP; python -m pytest tests/test_orchestrator_recovery.py tests/test_orchestrator_dispatch_service.py tests/test_orchestrator_execution_service.py tests/test_orchestrator_tasks.py tests/test_orchestrator_resume_task.py tests/test_langgraph_orchestrator_dispatch.py tests/test_langgraph_interrupt_resume.py tests/test_task_plan_resume.py tests/test_worktree_service.py -q
# 48 passed, 6 warnings in 19.55s
```

Concerns: the six warnings are existing `datetime.utcnow()`/event-loop
deprecations in agent/resume code; no Task 12 test failures remain.

## Review fix round 4

STATUS: COMPLETE — cancellation, callback, dispatch publication, and retry API
concurrency gaps are covered by new red-green regressions.

- Cancellation now locks and force-refreshes the parent plus children before
  atomically collecting known Celery IDs, incrementing the execution
  generation, and committing the terminal state.
- Prepare, prepare-wave, merge, and finalizer callbacks force-refresh the
  generation and terminal state at callback entry, before Worktree/CodeChange
  or database side effects, and at commit barriers. A cancel or retry that wins
  after callback entry returns a structured `skipped` result.
- Dispatch now persists `dispatch_prepared` with stable execution and Celery
  task IDs before publishing. A prepared retry republishes the same IDs;
  only `dispatch_queued` is an idempotent early return. Successful publication
  locks and transitions to queued.
- Publish failures lock and refresh current state. Concurrent cancellation or
  generation replacement is preserved, while a structured publish error is
  appended for diagnosis.
- In-place orchestrator retry returns HTTP 202, maps `ValueError` to 409 and
  `RuntimeError` to 503, while ordinary retry still creates a new task and
  retains HTTP 201.

Red command and real output:

```powershell
python -m pytest tests/test_orchestrator_recovery.py tests/test_orchestrator_dispatch_service.py tests/test_orchestrator_execution_service.py -q
# 12 failed, 19 passed in 36.72s
```

Verification commands and real outputs:

```powershell
python -m pytest tests/test_orchestrator_recovery.py tests/test_orchestrator_dispatch_service.py tests/test_orchestrator_execution_service.py -q
# 31 passed in 5.69s

python -m pytest tests/test_orchestrator_recovery.py tests/test_orchestrator_dispatch_service.py tests/test_orchestrator_execution_service.py tests/test_orchestrator_tasks.py tests/test_orchestrator_resume_task.py tests/test_langgraph_orchestrator_dispatch.py tests/test_langgraph_interrupt_resume.py tests/test_task_plan_resume.py tests/test_worktree_service.py -q
# 61 passed, 5 warnings in 74.24s

python -m pytest tests -q
# 256 passed, 1 skipped, 6 warnings in 137.30s
```

Concerns: the skipped test is the environment-gated PostgreSQL checkpointer
integration test. The six warnings are existing `datetime.utcnow()`
deprecations outside the Task 12 changes.
