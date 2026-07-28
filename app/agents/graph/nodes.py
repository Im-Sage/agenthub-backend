import json
from datetime import datetime
from typing import Any, Dict

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from app.agents.base import AgentRunRequest
from app.agents.graph.schemas import generate_orchestrator_plan
from app.agents.graph.state import AgentState
from app.agents.llm_factory import get_chat_llm
from app.db.session import SessionLocal
from app.models.task import Task
from app.schemas.enums import MessageType, SenderType, TaskStatus

def _child_ids_from_state(state: AgentState) -> list[int]:
    try:
        metadata = json.loads(state.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        return []
    child_ids = metadata.get("child_ids", [])
    return child_ids if isinstance(child_ids, list) else []


async def plan_node(state: AgentState) -> Dict[str, Any]:
    from app.services import task_service

    llm = get_chat_llm()
    user_goal = str(state["messages"][0].content)
    orchestrator_plan = await generate_orchestrator_plan(llm, user_goal)
    plan = [
        step.model_dump()
        for step in orchestrator_plan.steps
    ]

    db = SessionLocal()
    child_ids: list[int] = []
    try:
        parent_task = db.get(Task, state["task_id"])
        if parent_task:
            for step in plan:
                child_task = task_service.create_subtask(
                    db,
                    parent_task,
                    step["agent"],
                    step["instruction"],
                    task_type="graph_subtask",
                )
                child_ids.append(child_task.id)
                await task_service.broadcast_task_event(child_task, "task.created")
            plan_metadata = {
                "plan": plan,
                "child_ids": child_ids,
                "requires_plan_confirmation": True, # 虽然 plan 已经生成，但需要用户确认后才能执行
                "plan_status": "awaiting_confirmation",
            }
            parent_task.status = TaskStatus.PENDING
    # 使用 LLM 生成计划
            parent_task.result_summary = f"Orchestrator plan generated with {len(plan)} step(s). Awaiting confirmation."
            parent_task.metadata_json = json.dumps(plan_metadata, ensure_ascii=False)
            parent_task.finished_at = None
            db.commit()
            await task_service.broadcast_task_event(parent_task, "task.updated")
    finally:
        db.close()

    return {
                # planner 生成的每个步骤都创建一个子任务，并将子任务 ID 存储在 child_ids 中
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

async def approval_node(state: AgentState) -> Dict[str, Any]:
    # interrupt 是 langgraph 提供的一个函数，用于在执行过程中暂停并等待外部输入，这里用于等待用户对计划的批准或拒绝
    # LangGraph会将interrupt携带的数据暴露给调用方
    """
    假设用户点击，后端可能会通过
    Command(
    resume={
        "approved": True
        }
    )
    恢复后，LangGraph会将resume携带的数据传给approval_node的state中，state["approved"]就会是True
    """
    decision = interrupt(
        {
            # 这些数据都是从 state 中获取的，传递给调用方，让调用方知道当前任务的状态和计划内容
            "type": "orchestrator_plan_approval",
            "task_id": state["task_id"],
            "plan": state["plan"],
        }
    )

    approved = (
        bool(decision.get("approved")) # approved 是用户点击的结果，如果用户点击了批准，就会返回 True，否则返回 False
        if isinstance(decision, dict)
        else bool(decision)
    )

    return {
        "approval_status": "approved" if approved else "rejected",
        "awaiting_confirmation": False,
        "is_finished": not approved,
    }
async def execute_node(state: AgentState) -> Dict[str, Any]:
    from app.services import task_service

    if state.get("is_finished"):
        return {"errors": []}

    plan = state.get("plan") or []
    current_step_index = state["current_step_index"]
    if current_step_index >= len(plan):
        return {
            "is_finished": True,
            "current_agent": None,
    # current_step_index是当前执行的步骤索引，如果已经超过计划长度，说明执行完成，一共就有三个步骤，索引分别是0、1、2，如果current_step_index >= 3，就说明执行完成
            "current_instruction": None,
            "errors": [],
        }

    agent_code = state.get("current_agent") or plan[current_step_index]["agent"]
    instruction = state.get("current_instruction") or plan[current_step_index]["instruction"]
    repo_path = state.get("repo_path")
    child_ids = _child_ids_from_state(state)

    current_task_db_id = None
    if current_step_index < len(child_ids):
        current_task_db_id = child_ids[current_step_index]

    db = SessionLocal()
    child_task = db.get(Task, current_task_db_id) if current_task_db_id else None
    try:
        if child_task:
            child_task.status = TaskStatus.RUNNING
            child_task.started_at = datetime.utcnow()
            child_task.finished_at = None
            db.commit()
            await task_service.broadcast_task_event(child_task, "task.updated")
            await task_service.broadcast_task_log(child_task, f"Agent [{agent_code}] starting execution...")

        agent_obj = task_service.get_or_create_agent(db, agent_code)
        system_prompt = agent_obj.system_prompt or f"You are a {agent_code} engineer."
        context = {
            "agent_code": agent_code,
            "system_prompt": system_prompt,
        }

        if state.get("errors"):
            if child_task:
                await task_service.broadcast_task_log(
                    child_task,
                    "Detected previous errors, attempting self-healing...",
                )
            context["previous_error"] = state["errors"][-1]

        adapter = task_service.get_adapter(agent_obj)
        result = await adapter.run(
            AgentRunRequest(
                task_id=(
                    child_task.id
                    if child_task
                    else state["task_id"]
                ),
                conversation_id=state["conversation_id"],
                instruction=instruction,
                repo_path=repo_path,
                context=context,
                task=child_task,
            )
        )

        if result.status != "success":
            raise RuntimeError(
                result.summary or "Agent execution failed"
            )

        if child_task:
            child_task.status = TaskStatus.SUCCESS
            child_task.result_summary = result.summary
            child_task.finished_at = datetime.utcnow()
            db.commit()
            await task_service.broadcast_task_event(child_task, "task.updated")

        return {
            "execution_results": [
                {
                    "step": current_step_index,
                    "content": result.summary,
                    "files": result.changed_files,
                }
            ],
            "messages": [AIMessage(content=result.summary)],
            "errors": [],
        }
    except Exception as exc:
        if child_task:
            child_task.status = TaskStatus.FAILED
            child_task.error_message = str(exc)
            child_task.finished_at = datetime.utcnow()
            db.commit()
            await task_service.broadcast_task_event(child_task, "task.updated")
        return {"errors": [f"Execution error: {exc}"]}
    finally:
        db.close()


async def verify_node(state: AgentState) -> Dict[str, Any]:
    plan = state.get("plan") or []
    current_step_index = state["current_step_index"]
    if current_step_index >= len(plan):
        return {
            "is_finished": True,
            "current_agent": None,
            "current_instruction": None,
            "errors": [],
        }

    execution_results = state.get("execution_results") or []
    if not execution_results:
        return {"errors": ["No execution result was produced for verification."]}

    last_result = execution_results[-1]
    instruction = state.get("current_instruction") or ""

    wants_code = any(keyword in instruction.lower() for keyword in ["code", "代码", "写一个"])
    if state.get("repo_path") and wants_code and not last_result.get("files"):
        return {
            "errors": [
                "Code was requested but no [FILE: path] code block was produced, so no file could be saved."
            ]
        }

    content = str(last_result.get("content") or "")
    if "python" in instruction.lower() and "def" in content and "SyntaxError" in content:
        return {"errors": ["Potential Python syntax error detected."]}

    next_index = current_step_index + 1
    is_finished = next_index >= len(plan)

    return {
        "current_step_index": next_index,
        "current_agent": plan[next_index]["agent"] if not is_finished else None,
        "current_instruction": plan[next_index]["instruction"] if not is_finished else None,
        "is_finished": is_finished,
        "errors": [],
    }


async def summarize_node(state: AgentState) -> Dict[str, Any]:
    from app.services import task_service

    db = SessionLocal()
    try:
        parent_task = db.get(Task, state["task_id"])
        summary = (
            "LangGraph orchestrator completed.\n"
            f"Completed {len(state.get('plan') or [])} steps."
        )
        if parent_task:
            try:
                metadata = json.loads(parent_task.metadata_json or "{}")
            except json.JSONDecodeError:
                metadata = {}
            metadata["plan_status"] = "executed"
            parent_task.status = TaskStatus.SUCCESS
            parent_task.result_summary = summary
            parent_task.metadata_json = json.dumps(metadata, ensure_ascii=False)
            parent_task.finished_at = datetime.utcnow()
            db.commit()
            await task_service.broadcast_task_event(parent_task, "task.updated")

            from app.models.message import Message

            summary_msg = Message(
                conversation_id=parent_task.conversation_id,
                sender_type=SenderType.AGENT,
                sender_id=parent_task.agent_id,
                content=summary,
                message_type=MessageType.TEXT,
            )
            db.add(summary_msg)
            db.commit()
            await task_service.broadcast_agent_message(summary_msg)

        return {"final_summary": summary}
    finally:
        db.close()
