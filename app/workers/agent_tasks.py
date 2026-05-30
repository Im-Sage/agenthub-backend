import asyncio
from sqlalchemy import select
from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.task import Task
from app.models.agent import Agent
from app.models.message import Message
from app.schemas.enums import TaskStatus, SenderType, MessageType
from app.agents.base import AgentRunRequest
from app.services import task_service


def sync_run_async(coro):
    """在同步环境运行异步协程的辅助函数"""
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # 如果当前已经有运行中的 loop（比如在某些测试环境），直接运行
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    else:
        return asyncio.run(coro)

"""
负责单个 Agent 的异步执行，
包括状态更新
（PENDING -> RUNNING -> SUCCESS/FAILED）和结果存储。
"""
@celery_app.task(name="app.workers.agent_tasks.run_agent_task")
def run_agent_task(task_id: int, create_reply_message: bool = True):
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return "Task not found"

        task.status = TaskStatus.RUNNING
        db.commit()
        db.refresh(task)
        # TODO: Phase 3 - 发送 WebSocket 推送 (task.updated)

        agent = db.get(Agent, task.agent_id)
        if agent is None:
            raise RuntimeError("任务关联的 Agent 不存在。")

        adapter = task_service.get_adapter(agent)
        
        # 调用异步适配器
        result = sync_run_async(
            adapter.run(
                AgentRunRequest(
                    task_id=task.id,
                    conversation_id=task.conversation_id,
                    instruction=task.instruction,
                    context={"system_prompt": agent.system_prompt or ""},
                )
            )
        )

        task.status = TaskStatus.SUCCESS if result.status == "success" else TaskStatus.FAILED
        task.result_summary = result.summary
        db.commit()
        db.refresh(task)
        # TODO: Phase 3 - 发送 WebSocket 推送 (task.updated)

        # 创建 Agent 消息
        if create_reply_message:
            agent_message = Message(
                conversation_id=task.conversation_id,
                sender_type=SenderType.AGENT,
                sender_id=task.agent_id,
                content=result.summary,
                message_type=MessageType.TEXT,
            )
            db.add(agent_message)
            db.commit()
            db.refresh(agent_message)
            # TODO: Phase 3 - 发送 WebSocket 推送 (message.created)
            
        return f"Task {task_id} completed: {task.status}"
    except Exception as exc:
        task = db.get(Task, task_id)
        if task is not None:
            task.status = TaskStatus.FAILED
            task.error_message = str(exc)
            db.commit()
        return f"Task {task_id} failed: {str(exc)}"
    finally:
        db.close()


@celery_app.task(name="app.workers.agent_tasks.run_orchestrator_task")
def run_orchestrator_task(parent_task_id: int):
    db = SessionLocal()
    try:
        parent_task = db.get(Task, parent_task_id)
        if parent_task is None:
            return "Parent task not found"

        parent_task.status = TaskStatus.RUNNING
        db.commit()
        db.refresh(parent_task)
        # TODO: Phase 3 - 发送 WebSocket 推送 (task.updated)

        child_ids = list(
            db.scalars(
                select(Task.id)
                .where(Task.parent_task_id == parent_task.id)
                .order_by(Task.id.asc())
            )
        )
    finally:
        db.close()

    # 顺序执行子任务
    for child_id in child_ids:
        run_agent_task(child_id, create_reply_message=True)

    db = SessionLocal()
    try:
        parent_task = db.get(Task, parent_task_id)
        if parent_task is None:
            return

        child_tasks = list(
            db.scalars(
                select(Task)
                .where(Task.parent_task_id == parent_task.id)
                .order_by(Task.id.asc())
            )
        )
        failed_tasks = [task for task in child_tasks if task.status == TaskStatus.FAILED]
        if failed_tasks:
            parent_task.status = TaskStatus.FAILED
            parent_task.error_message = "存在子任务执行失败。"
        else:
            parent_task.status = TaskStatus.SUCCESS
            parent_task.result_summary = task_service.build_orchestrator_summary(parent_task, child_tasks)

        db.commit()
        db.refresh(parent_task)
        # TODO: Phase 3 - 发送 WebSocket 推送 (task.updated)

        if parent_task.result_summary:
            summary_message = Message(
                conversation_id=parent_task.conversation_id,
                sender_type=SenderType.AGENT,
                sender_id=parent_task.agent_id,
                content=parent_task.result_summary,
                message_type=MessageType.TEXT,
            )
            db.add(summary_message)
            db.commit()
            db.refresh(summary_message)
            # TODO: Phase 3 - 发送 WebSocket 推送 (message.created)
            
        return f"Orchestrator Task {parent_task_id} completed"
    finally:
        db.close()
