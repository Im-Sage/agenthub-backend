import json
import re
from datetime import datetime
from typing import Any, Dict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt

from app.agents.graph.state import AgentState
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.task import Task
from app.schemas.enums import MessageType, SenderType, TaskStatus
from app.services.workspace_service import workspace_service


def get_llm():
    return ChatOpenAI(
        model=settings.aliyun_model,
        openai_api_key=settings.aliyun_api_key,
        openai_api_base=settings.aliyun_base_url,
        timeout=settings.aliyun_timeout_seconds,
        temperature=0,
    )


def _extract_plan(content: str) -> list[dict[str, str]]:
    try:
        match = re.search(r"\[[\s\S]*\]", content)
        raw_plan = json.loads(match.group()) if match else []
    except Exception:
        raw_plan = []

    plan: list[dict[str, str]] = []
    for step in raw_plan:
        if not isinstance(step, dict):
            continue
        agent = str(step.get("agent") or "backend").strip()
        instruction = str(step.get("instruction") or "").strip()
        if not instruction:
            continue
        if agent not in {"backend", "frontend", "reviewer"}:
            agent = "backend"
        plan.append({"agent": agent, "instruction": instruction})

    if plan:
        return plan

    return [{"agent": "backend", "instruction": content.strip() or "Handle the user request."}]


def _child_ids_from_state(state: AgentState) -> list[int]:
    try:
        metadata = json.loads(state.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        return []
    child_ids = metadata.get("child_ids", [])
    return child_ids if isinstance(child_ids, list) else []


async def plan_node(state: AgentState) -> Dict[str, Any]:
    from app.services import task_service

    llm = get_llm()
    messages = [
        SystemMessage(
            content=(
                "You are a software task orchestrator. Split the user goal into a JSON array only. "
                "Each item must contain agent and instruction. agent must be one of backend, "
                "frontend, reviewer. Do not include Markdown or explanatory text."
            )
        ),
        HumanMessage(content=f"User goal: {state['messages'][0].content}"),
    ]

    response = await llm.ainvoke(messages)
    plan = _extract_plan(str(response.content))

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
                "requires_plan_confirmation": True,
                "plan_status": "awaiting_confirmation",
            }
            parent_task.status = TaskStatus.PENDING
            parent_task.result_summary = f"Orchestrator plan generated with {len(plan)} step(s). Awaiting confirmation."
            parent_task.metadata_json = json.dumps(plan_metadata, ensure_ascii=False)
            parent_task.finished_at = None
            db.commit()
            await task_service.broadcast_task_event(parent_task, "task.updated")
    finally:
        db.close()

    return {
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
            "current_instruction": None,
            "errors": [],
        }

    llm = get_llm()
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
        if repo_path:
            system_prompt += (
                f"\nCurrent workspace: {repo_path}\n"
                "When changing files, use these exact file operation markers:\n"
                "[FILE: relative/path]\n```language\ncontent\n```\n"
                "[DELETE: relative/path]\n"
                "[RENAME: old/relative/path -> new/relative/path]\n"
                "Do not output code blocks without a [FILE: ] marker."
            )

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=instruction)]

        if state.get("errors"):
            if child_task:
                await task_service.broadcast_task_log(
                    child_task,
                    "Detected previous errors, attempting self-healing...",
                )
            messages.append(
                HumanMessage(
                    content=(
                        "Previous execution failed with this error:\n"
                        f"{state['errors'][-1]}\n"
                        "Fix the issue and answer again."
                    )
                )
            )

        response = await llm.ainvoke(messages)
        content = str(response.content)

        changed_files: list[str] = []
        if repo_path:
            from app.tools.agent_file_ops import apply_file_operations_with_tools
            changed_files = await apply_file_operations_with_tools(
                local_path=repo_path,
                content=content,
                task_id=child_task.id if child_task else None,
                conversation_id=state.get("conversation_id"),
            )

        if child_task:
            child_task.status = TaskStatus.SUCCESS
            child_task.result_summary = content
            child_task.finished_at = datetime.utcnow()
            db.commit()
            await task_service.broadcast_task_event(child_task, "task.updated")

        return {
            "execution_results": [
                {"step": current_step_index, "content": content, "files": changed_files}
            ],
            "messages": [response],
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
