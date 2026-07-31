import json
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.base import AgentAdapter
from app.agents.langgraph_adapter import LangGraphOrchestratorAdapter
from app.agents.mock_adapter import MockAgentAdapter
from app.agents.qwen_adapter import QwenAgentAdapter
from app.core.config import settings
from app.models.agent import Agent
from app.models.code_change import CodeChange
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.repository import Repository
from app.models.task import Task
from app.schemas.enums import AgentAdapterType, TaskStatus
from app.core.logging import get_logger
from app.services import event_service


MOCK_COMMAND = "@mock"
QWEN_COMMAND = "@qwen"
ORCHESTRATOR_COMMAND = "@orchestrator"
logger = get_logger("task_service")


@dataclass(frozen=True)
class AgentDefinition:
    code: str
    name: str
    adapter_type: str
    system_prompt: str
    capabilities: str


AGENT_DEFINITIONS = {
    "mock": AgentDefinition(
        code="mock",
        name="Mock Agent",
        adapter_type="mock",
        system_prompt="Mock agent for local integration tests.",
        capabilities="Echo tasks, validate task flow, and test WebSocket events.",
    ),
    "qwen": AgentDefinition(
        code="qwen",
        name="Qwen Agent",
        adapter_type="qwen",
        system_prompt="Use Qwen to handle general LLM tasks.",
        capabilities="LLM responses, requirement analysis, and solution generation.",
    ),
    "orchestrator": AgentDefinition(
        code="orchestrator",
        name="Orchestrator Agent",
        adapter_type="langgraph",
        system_prompt=(
            "You are a software task orchestrator. Split the user goal into a JSON array only. "
            "Each item must contain agent and instruction. agent must be one of backend, "
            "frontend, reviewer. Do not include Markdown or explanatory text."
        ),
        capabilities="Requirement analysis, task planning, agent assignment, and workflow execution.",
    ),
    "backend": AgentDefinition(
        code="backend",
        name="Backend Agent",
        adapter_type="qwen",
        system_prompt="Responsible for backend APIs, data models, and service logic.",
        capabilities="FastAPI, SQLAlchemy, and API design.",
    ),
    "frontend": AgentDefinition(
        code="frontend",
        name="Frontend Agent",
        adapter_type="qwen",
        system_prompt="Responsible for frontend pages, interaction, and state presentation.",
        capabilities="Page design, interaction implementation, and API integration.",
    ),
    "reviewer": AgentDefinition(
        code="reviewer",
        name="Reviewer Agent",
        adapter_type="qwen",
        system_prompt="Responsible for reviewing quality, risks, and test coverage.",
        capabilities="Code review, risk identification, and test suggestions.",
    ),
}


def parse_command_instruction(content: str, command: str) -> str | None:
    stripped_content = content.strip()
    if not stripped_content.startswith(command):
        return None

    instruction = stripped_content.removeprefix(command).strip()
    return instruction or "Please handle this message."


def parse_mock_instruction(content: str) -> str | None:
    return parse_command_instruction(content, MOCK_COMMAND)


def parse_qwen_instruction(content: str) -> str | None:
    return parse_command_instruction(content, QWEN_COMMAND)


def parse_orchestrator_goal(content: str) -> str | None:
    return parse_command_instruction(content, ORCHESTRATOR_COMMAND)


def get_or_create_agent(db: Session, code: str) -> Agent:
    definition = AGENT_DEFINITIONS[code]
    agent = db.scalar(select(Agent).where(Agent.code == code))

    if agent is not None:
        if (
            agent.name != definition.name
            or agent.adapter_type != definition.adapter_type
            or agent.system_prompt != definition.system_prompt
            or agent.capabilities != definition.capabilities
        ):
            agent.name = definition.name
            agent.adapter_type = definition.adapter_type
            agent.system_prompt = definition.system_prompt
            agent.capabilities = definition.capabilities
            db.commit()
            db.refresh(agent)
        return agent

    agent = Agent(
        name=definition.name,
        code=definition.code,
        adapter_type=definition.adapter_type,
        system_prompt=definition.system_prompt,
        capabilities=definition.capabilities,
        enabled=True,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


def get_adapter(agent: Agent) -> AgentAdapter:
    if agent.adapter_type == "langgraph":
        return LangGraphOrchestratorAdapter()
    if agent.adapter_type in [AgentAdapterType.QWEN, "aliyun_qwen", "qwen"]:
        return QwenAgentAdapter()
    return MockAgentAdapter()


def create_single_agent_task_from_message(
    db: Session,
    conversation: Conversation,
    content: str,
    command: str,
    agent_code: str,
) -> Task | None:
    instruction = parse_command_instruction(content, command)
    if instruction is None:
        return None

    agent = get_or_create_agent(db, agent_code)
    task = Task(
        conversation_id=conversation.id,
        agent_id=agent.id,
        status=TaskStatus.PENDING,
        instruction=instruction,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def create_mock_task_from_message(db: Session, conversation: Conversation, content: str) -> Task | None:
    return create_single_agent_task_from_message(db, conversation, content, MOCK_COMMAND, "mock")


def create_qwen_task_from_message(db: Session, conversation: Conversation, content: str) -> Task | None:
    return create_single_agent_task_from_message(db, conversation, content, QWEN_COMMAND, "qwen")


def build_orchestrator_subtasks(goal: str) -> list[tuple[str, str]]:
    return [
        ("backend", f"Design backend data models, APIs, and service flow for: {goal}"),
        ("frontend", f"Design frontend pages, interaction states, and API integration for: {goal}"),
        ("reviewer", f"Review the full plan, risks, edge cases, and testing focus for: {goal}"),
    ]


def create_orchestrator_tasks_from_message(
    db: Session,
    conversation: Conversation,
    content: str,
) -> Task | None:
    goal = parse_orchestrator_goal(content)
    if goal is None:
        return None

    orchestrator_agent = get_or_create_agent(db, "orchestrator")
    parent_task = Task(
        conversation_id=conversation.id,
        agent_id=orchestrator_agent.id,
        status=TaskStatus.PENDING,
        instruction=goal,
        metadata_json=json.dumps({"requires_plan_confirmation": True, "plan_status": "planning"}),
    )
    db.add(parent_task)
    db.commit()
    db.refresh(parent_task)
    return parent_task


def ensure_user_task_capacity(db: Session, user_id: int) -> None:
    active_statuses = [TaskStatus.PENDING.value, TaskStatus.RUNNING.value, TaskStatus.PENDING, TaskStatus.RUNNING]
    active_count = db.scalar(
        select(func.count(Task.id))
        .join(Conversation, Conversation.id == Task.conversation_id)
        .where(Conversation.user_id == user_id)
        .where(Task.status.in_(active_statuses))
    )
    if active_count is not None and active_count >= settings.max_concurrent_tasks_per_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Concurrent task limit exceeded. "
                f"Max active tasks per user: {settings.max_concurrent_tasks_per_user}."
            ),
        )


def create_subtask(
    db: Session,
    parent_task: Task,
    agent_code: str,
    instruction: str,
    task_type: str | None = None,
    depends_on: list[str] | None = None,
    step_key: str | None = None,
    step_index: int | None = None,
    write_scope: list[str] | None = None,
) -> Task:
    agent = get_or_create_agent(db, agent_code)
    step_metadata = (
        {
            "step_key": step_key,
            "step_index": step_index,
            "write_scope": list(write_scope or []),
        }
        if step_key is not None
        else None
    )
    child_task = Task(
        conversation_id=parent_task.conversation_id,
        parent_task_id=parent_task.id,
        agent_id=agent.id,
        status=TaskStatus.PENDING,
        instruction=instruction,
        task_type=task_type,
        depends_on=(
            json.dumps(depends_on)
            if depends_on is not None
            else None
        ),
        step_key=step_key,
        step_index=step_index,
        write_scope_json=(
            json.dumps(write_scope, ensure_ascii=False)
            if write_scope is not None
            else None
        ),
        metadata_json=(
            json.dumps(step_metadata, ensure_ascii=False)
            if step_metadata is not None
            else None
        ),
    )
    db.add(child_task)
    db.commit()
    db.refresh(child_task)
    return child_task


def create_retry_task(db: Session, task: Task) -> Task:
    if task.status not in [TaskStatus.FAILED, TaskStatus.FAILED.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task must be FAILED before retrying. Current status: {task.status}.",
        )

    metadata = {}
    if task.metadata_json:
        try:
            metadata = json.loads(task.metadata_json)
        except json.JSONDecodeError:
            metadata = {}
    metadata["retry_of_task_id"] = task.id

    retry_task = Task(
        conversation_id=task.conversation_id,
        parent_task_id=task.parent_task_id,
        agent_id=task.agent_id,
        status=TaskStatus.PENDING,
        task_type=task.task_type,
        instruction=task.instruction,
        depends_on=task.depends_on,
        metadata_json=json.dumps(metadata, ensure_ascii=False),
        retry_count=(task.retry_count or 0) + 1,
    )
    db.add(retry_task)
    db.commit()
    db.refresh(retry_task)
    return retry_task


def get_task_metadata(task: Task) -> dict:
    if not task.metadata_json:
        return {}
    try:
        data = json.loads(task.metadata_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def get_orchestrator_plan(task: Task) -> dict:
    metadata = get_task_metadata(task)
    return {
        "task_id": task.id,
        "plan_status": metadata.get("plan_status", "none"),
        "requires_confirmation": bool(metadata.get("requires_plan_confirmation")),
        "plan": metadata.get("plan", []),
        "child_ids": metadata.get("child_ids", []),
    }


def is_orchestrator_task(task: Task) -> bool:
    metadata = get_task_metadata(task)
    return bool(
        isinstance(metadata.get("plan"), list)
        or (
            getattr(task, "agent", None) is not None
            and task.agent.adapter_type == "langgraph"
        )
    )


def confirm_orchestrator_plan(db: Session, task: Task) -> Task:
    metadata = get_task_metadata(task)
    if metadata.get("plan_status") != "awaiting_confirmation":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task plan must be awaiting confirmation. Current status: {metadata.get('plan_status', 'none')}.",
        )
    plan = metadata.get("plan")
    if not isinstance(plan, list) or not plan:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task plan is empty and cannot be confirmed.",
        )

    metadata["plan_status"] = "confirmed"
    metadata["confirmed_at"] = datetime.utcnow().isoformat()
    task.metadata_json = json.dumps(metadata, ensure_ascii=False)
    task.status = TaskStatus.PENDING
    task.finished_at = None
    db.commit()
    db.refresh(task)
    return task


async def broadcast_task_event(task: Task, event_name: str) -> None:
    logger.info(
        "broadcast_task_event task_id=%s conversation_id=%s event=%s status=%s",
        task.id,
        task.conversation_id,
        event_name,
        task.status,
    )
    await event_service.publish_task_event(task, event_name)


async def broadcast_task_log(task: Task, message: str) -> None:
    await event_service.publish_task_log(task, message)


async def broadcast_agent_message(message: Message) -> None:
    await event_service.publish_message_event(message)


def build_orchestrator_summary(parent_task: Task, child_tasks: list[Task]) -> str:
    lines = [f"Orchestrator completed task planning and execution: {parent_task.instruction}"]
    for index, task in enumerate(child_tasks, start=1):
        lines.append(f"{index}. Subtask {task.id}: {task.result_summary or task.instruction}")
    return "\n".join(lines)


def list_tasks(db: Session, user_id: int, conversation_id: int) -> list[Task]:
    get_owned_conversation(db, conversation_id, user_id)
    statement = (
        select(Task)
        .where(Task.conversation_id == conversation_id)
        .order_by(Task.created_at.asc(), Task.id.asc())
    )
    return list(db.scalars(statement))


def get_task(db: Session, user_id: int, task_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    get_owned_conversation(db, task.conversation_id, user_id)
    return task


def list_child_tasks(db: Session, user_id: int, task_id: int) -> list[Task]:
    parent_task = get_task(db, user_id, task_id)
    statement = select(Task).where(Task.parent_task_id == parent_task.id).order_by(Task.id.asc())
    return list(db.scalars(statement))


def get_owned_conversation(db: Session, conversation_id: int, user_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def get_owned_task(db: Session, task_id: int, user_id: int) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    get_owned_conversation(db, task.conversation_id, user_id)
    return task


def get_owned_repository(db: Session, repository_id: int, user_id: int) -> Repository:
    repository = db.get(Repository, repository_id)
    if repository is None or repository.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


def get_owned_code_change(db: Session, code_change_id: int, user_id: int) -> CodeChange:
    code_change = db.get(CodeChange, code_change_id)
    if code_change is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CodeChange not found")

    get_owned_task(db, code_change.task_id, user_id)
    return code_change

