import asyncio
from datetime import datetime
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.models import Task, Agent, Message, Repository, Conversation
from app.schemas.enums import TaskStatus, SenderType, MessageType
from app.agents.base import AgentRunRequest
from app.agents.langgraph_adapter import LangGraphOrchestratorAdapter
from app.core.config import settings
from app.core.logging import get_logger
from app.services import task_service
from app.services.workspace_service import workspace_service
from app.tools import register_builtin_tools

# 注册内置工具
register_builtin_tools()

logger = get_logger("worker.agent_tasks")


def maybe_generate_revision_code_change(db, task: Task, conversation: Conversation):
    if task.status not in [TaskStatus.SUCCESS, TaskStatus.SUCCESS.value] or task.task_type != "revision":
        return None
    if conversation is None or not conversation.repository_id:
        return None

    repo = db.get(Repository, conversation.repository_id)
    if repo is None:
        return None

    from app.services import event_service, repo_service

    try:
        code_change = sync_run_async(repo_service.generate_code_change(db, task, repo))
        sync_run_async(event_service.publish_code_change_event(task.conversation_id, code_change))
        return code_change
    except SoftTimeLimitExceeded as exc:
        db.rollback()
        error_detail = f"Task exceeded soft time limit: {settings.task_soft_time_limit_seconds}s"
        logger.exception("task_timeout task_id=%s error=%s", task_id, error_detail)
        task = db.get(Task, task_id)
        if task is not None:
            task.status = TaskStatus.FAILED
            task.error_message = error_detail
            task.finished_at = datetime.utcnow()
            db.commit()
            sync_run_async(task_service.broadcast_task_event(task, "task.updated"))
        return f"Task {task_id} failed: {error_detail}"
    except Exception as exc:
        sync_run_async(task_service.broadcast_task_log(task, f"Failed to auto-generate CodeChange: {exc}"))
        return None


def sync_run_async(coro):
    """在同步环境运行异步协程的稳健辅助函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        # 如果当前已有运行中的 loop，将协程提交到该 loop
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    else:
        # 如果有 loop 但未运行，直接运行直到完成
        return loop.run_until_complete(coro)

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
        task.started_at = datetime.utcnow()
        task.finished_at = None
        db.commit()
        db.refresh(task)
        # 实时推送：任务进入运行状态
        sync_run_async(task_service.broadcast_task_event(task, "task.updated"))

        agent = db.get(Agent, task.agent_id)
        if agent is None:
            raise RuntimeError("任务关联的 Agent 不存在。")

        adapter = task_service.get_adapter(agent)
        
        # 获取工作空间上下文 (通过会话关联的仓库)
        from app.models.repository import Repository
        from app.models.conversation import Conversation
        
        repo_path = None
        conversation = db.get(Conversation, task.conversation_id)
        if conversation and conversation.repository_id:
            repo = db.get(Repository, conversation.repository_id)
            if repo:
                repo_path = repo.local_path
                # 准备工作区分支并清理历史干扰 (Phase 10 鲁棒性增强)
                sync_run_async(workspace_service.prepare_branch(
                    repo_path, repo.default_branch, f"agent-task-{task.id}", task=task
                ))

        # 调用异步适配器
        result = sync_run_async(
            adapter.run(
                AgentRunRequest(
                    task_id=task.id,
                    conversation_id=task.conversation_id,
                    instruction=task.instruction,
                    repo_path=repo_path,
                    context={"system_prompt": agent.system_prompt or ""},
                    task=task
                )
            )
        )

        task.status = TaskStatus.SUCCESS if result.status == "success" else TaskStatus.FAILED
        task.result_summary = result.summary
        task.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(task)
        # 实时推送：任务执行完成
        sync_run_async(task_service.broadcast_task_event(task, "task.updated"))

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
            # 实时推送：Agent 回复消息
            sync_run_async(task_service.broadcast_agent_message(agent_message))
        maybe_generate_revision_code_change(db, task, conversation)

        return f"Task {task_id} completed: {task.status}"
    except SoftTimeLimitExceeded as exc:
        db.rollback()
        error_detail = f"Task exceeded soft time limit: {settings.task_soft_time_limit_seconds}s"
        logger.exception("orchestrator_timeout task_id=%s error=%s", parent_task_id, error_detail)
        parent_task = db.get(Task, parent_task_id)
        if parent_task:
            parent_task.status = TaskStatus.FAILED
            parent_task.error_message = error_detail
            parent_task.finished_at = datetime.utcnow()
            db.commit()
            sync_run_async(task_service.broadcast_task_event(parent_task, "task.updated"))
        return f"Orchestrator failed: {error_detail}"
    except Exception as exc:
        db.rollback()  # 发生异常时回滚，确保后续更新能成功
        error_detail = f"{type(exc).__name__}: {str(exc)}" if str(exc) else repr(exc)
        logger.exception("task_failed task_id=%s error=%s", task_id, error_detail)
        task = db.get(Task, task_id)
        if task is not None:
            task.status = TaskStatus.FAILED
            task.error_message = error_detail
            task.finished_at = datetime.utcnow()
            db.commit()
            sync_run_async(task_service.broadcast_task_event(task, "task.updated"))
        return f"Task {task_id} failed: {error_detail}"
    finally:
        db.close()


import json
import re

def clean_json_response(content: str) -> str:
    """清理 LLM 返回的 JSON 字符串（更稳健的处理方式）"""
    # 1. 尝试匹配标准的 markdown JSON 代码块
    pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(pattern, content)
    if match:
        return match.group(1).strip()
    
    # 2. 如果没找到代码块，尝试通过找首尾的 [ 和 ] 来抠出 JSON 数组
    # 这个正则寻找第一个 [ 和最后一个 ] 之间的所有内容
    pattern_bracket = r"(\[[\s\S]*\])"
    match_bracket = re.search(pattern_bracket, content)
    if match_bracket:
        return match_bracket.group(1).strip()

    # 3. 兜底：直接返回原始字符串
    return content.strip()

@celery_app.task(name="app.workers.agent_tasks.run_orchestrator_task")
def run_orchestrator_task(parent_task_id: int):
    db = SessionLocal()
    try:
        parent_task = db.get(Task, parent_task_id)
        if parent_task is None:
            return "Parent task not found"

        parent_task.status = TaskStatus.RUNNING
        parent_task.started_at = datetime.utcnow()
        parent_task.finished_at = None
        db.commit()
        db.refresh(parent_task)
        sync_run_async(task_service.broadcast_task_event(parent_task, "task.updated"))

        # 运行 Orchestrator Agent (现在已切换为 LangGraph 适配器)
        agent = db.get(Agent, parent_task.agent_id)
        adapter = task_service.get_adapter(agent)
        
        # 获取工作空间上下文
        repo_path = None
        conversation = db.get(Conversation, parent_task.conversation_id)
        if conversation and conversation.repository_id:
            repo = db.get(Repository, conversation.repository_id)
            if repo:
                repo_path = repo.local_path

        run_result = sync_run_async(
            adapter.run(
                AgentRunRequest(
                    task_id=parent_task.id,
                    conversation_id=parent_task.conversation_id,
                    instruction=parent_task.instruction,
                    repo_path=repo_path,
                    context={"system_prompt": agent.system_prompt or ""},
                    task=parent_task
                )
            )
        )

        if run_result.status == "awaiting_confirmation":
            parent_task = db.get(Task, parent_task_id)
            if parent_task:
                parent_task.status = TaskStatus.PENDING
                parent_task.finished_at = None
                db.commit()
                db.refresh(parent_task)
                sync_run_async(
                    task_service.broadcast_task_event(
                        parent_task,
                        "task.updated",
                    )
                )
            return (
                "LangGraph Orchestrator awaiting confirmation: "
                f"{run_result.summary}"
            )

        return f"LangGraph Orchestrator completed: {run_result.summary}"
    except Exception as exc:
        db.rollback()
        error_detail = f"{type(exc).__name__}: {str(exc)}"
        logger.exception("orchestrator_failed task_id=%s error=%s", parent_task_id, error_detail)
        parent_task = db.get(Task, parent_task_id)
        if parent_task:
            parent_task.status = TaskStatus.FAILED
            parent_task.error_message = error_detail
            parent_task.finished_at = datetime.utcnow()
            db.commit()
            sync_run_async(task_service.broadcast_task_event(parent_task, "task.updated"))
        return f"Orchestrator failed: {error_detail}"
    finally:
        db.close()


@celery_app.task(name="app.workers.agent_tasks.resume_orchestrator_task")
def resume_orchestrator_task(parent_task_id: int, resume_value: dict):
    db = SessionLocal()
    try:
        parent_task = db.get(Task, parent_task_id)
        if parent_task is None:
            return "Parent task not found"

        parent_task.status = TaskStatus.RUNNING
        parent_task.finished_at = None
        db.commit()
        db.refresh(parent_task)
        sync_run_async(
            task_service.broadcast_task_event(parent_task, "task.updated")
        )

        adapter = LangGraphOrchestratorAdapter()
        run_result = sync_run_async(
            adapter.resume(parent_task_id, resume_value)
        )

        return f"LangGraph Orchestrator resumed: {run_result.summary}"
    except Exception as exc:
        db.rollback()
        error_detail = f"{type(exc).__name__}: {str(exc)}"
        logger.exception(
            "orchestrator_resume_failed task_id=%s error=%s",
            parent_task_id,
            error_detail,
        )
        parent_task = db.get(Task, parent_task_id)
        if parent_task:
            parent_task.status = TaskStatus.FAILED
            parent_task.error_message = error_detail
            parent_task.finished_at = datetime.utcnow()
            db.commit()
            sync_run_async(
                task_service.broadcast_task_event(parent_task, "task.updated")
            )
        return f"Orchestrator resume failed: {error_detail}"
    finally:
        db.close()
