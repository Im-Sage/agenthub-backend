import asyncio
from sqlalchemy import select
from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.models import Task, Agent, Message, Repository, Conversation
from app.schemas.enums import TaskStatus, SenderType, MessageType
from app.agents.base import AgentRunRequest
from app.services import task_service


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

        # 调用异步适配器
        result = sync_run_async(
            adapter.run(
                AgentRunRequest(
                    task_id=task.id,
                    conversation_id=task.conversation_id,
                    instruction=task.instruction,
                    repo_path=repo_path,
                    context={"system_prompt": agent.system_prompt or ""},
                )
            )
        )

        task.status = TaskStatus.SUCCESS if result.status == "success" else TaskStatus.FAILED
        task.result_summary = result.summary
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
            
        return f"Task {task_id} completed: {task.status}"
    except Exception as exc:
        db.rollback()  # 发生异常时回滚，确保后续更新能成功
        error_detail = f"{type(exc).__name__}: {str(exc)}" if str(exc) else repr(exc)
        print(f"[Worker] Task {task_id} raised exception: {error_detail}")
        task = db.get(Task, task_id)
        if task is not None:
            task.status = TaskStatus.FAILED
            task.error_message = error_detail
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
        db.commit()
        db.refresh(parent_task)
        sync_run_async(task_service.broadcast_task_event(parent_task, "task.updated"))

        # 1. 运行 Orchestrator Agent 获取拆解方案 (JSON)
        agent = db.get(Agent, parent_task.agent_id)
        adapter = task_service.get_adapter(agent)
        
        run_result = sync_run_async(
            adapter.run(
                AgentRunRequest(
                    task_id=parent_task.id,
                    conversation_id=parent_task.conversation_id,
                    instruction=f"请拆解以下目标：{parent_task.instruction}",
                    context={"system_prompt": agent.system_prompt or ""},
                )
            )
        )

        # 2. 解析 JSON 并创建子任务
        try:
            plan_json = clean_json_response(run_result.summary)
            subtasks_data = json.loads(plan_json)
            if not isinstance(subtasks_data, list):
                raise ValueError("LLM 返回的不是 JSON 数组")
            
            child_ids = []
            for item in subtasks_data:
                child_task = task_service.create_subtask(
                    db=db,
                    parent_task=parent_task,
                    agent_code=item.get("agent", "backend"),
                    instruction=item.get("instruction", ""),
                    task_type="dynamic_subtask"
                )
                child_ids.append(child_task.id)
                # 推送子任务创建事件
                sync_run_async(task_service.broadcast_task_event(child_task, "task.created"))
            
            db.commit()
        except Exception as e:
            parent_task.status = TaskStatus.FAILED
            parent_task.error_message = f"解析方案失败: {e}\n原始回复: {run_result.summary}"
            db.commit()
            sync_run_async(task_service.broadcast_task_event(parent_task, "task.updated"))
            return f"Failed to parse plan: {e}"

        # 3. 顺序执行生成的子任务
        for child_id in child_ids:
            # 可以在这里根据 depends_on 做更复杂的逻辑，目前先简单顺序执行
            run_agent_task(child_id, create_reply_message=True)

        # 4. 汇总结果
        db.refresh(parent_task)
        child_tasks = list(
            db.scalars(
                select(Task)
                .where(Task.parent_task_id == parent_task.id)
                .order_by(Task.id.asc())
            )
        )
        failed_tasks = [t for t in child_tasks if t.status == TaskStatus.FAILED]
        if failed_tasks:
            parent_task.status = TaskStatus.FAILED
            # 汇总所有失败子任务的错误信息，处理 error_message 为 None 的情况
            errors = []
            for t in failed_tasks:
                msg = t.error_message or t.result_summary or "无详细错误信息"
                errors.append(f"子任务 {t.id} ({t.instruction[:20]}...): {msg}")
            parent_task.error_message = "子任务执行失败：\n" + "\n".join(errors)
        else:
            parent_task.status = TaskStatus.SUCCESS
            parent_task.result_summary = task_service.build_orchestrator_summary(parent_task, child_tasks)

        db.commit()
        db.refresh(parent_task)
        sync_run_async(task_service.broadcast_task_event(parent_task, "task.updated"))

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
            sync_run_async(task_service.broadcast_agent_message(summary_message))
            
        return f"Orchestrator Task {parent_task_id} completed with {len(child_ids)} subtasks"
    except Exception as exc:
        if 'parent_task' in locals() and parent_task:
            parent_task.status = TaskStatus.FAILED
            parent_task.error_message = str(exc)
            db.commit()
        return f"Orchestrator Task {parent_task_id} failed: {str(exc)}"
    finally:
        db.close()
