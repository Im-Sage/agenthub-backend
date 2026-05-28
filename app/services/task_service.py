from dataclasses import dataclass

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import AgentAdapter, AgentRunRequest
from app.agents.mock_adapter import MockAgentAdapter
from app.agents.qwen_adapter import QwenAgentAdapter
from app.core.websocket_manager import websocket_manager
from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.task import Task
from app.schemas.message import MessageRead, WebSocketMessageEvent
from app.schemas.task import TaskEvent, TaskRead


MOCK_COMMAND = "@mock"
QWEN_COMMAND = "@qwen"
ORCHESTRATOR_COMMAND = "@orchestrator"


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
        system_prompt="用于本地联调的模拟 Agent。",
        capabilities="任务回显、流程联调、WebSocket 推送验证",
    ),
    "qwen": AgentDefinition(
        code="qwen",
        name="Qwen Agent",
        adapter_type="aliyun_qwen",
        system_prompt="使用阿里通义千问处理真实 LLM 任务。",
        capabilities="真实 LLM 回复、需求分析、方案生成",
    ),
    "orchestrator": AgentDefinition(
        code="orchestrator",
        name="Orchestrator Agent",
        adapter_type="mock",
        system_prompt="负责分析用户目标并拆解为多个子任务。",
        capabilities="需求分析、任务拆解、Agent 分派",
    ),
    "backend": AgentDefinition(
        code="backend",
        name="Backend Agent",
        adapter_type="mock",
        system_prompt="负责后端接口、数据模型和服务逻辑。",
        capabilities="FastAPI、SQLAlchemy、接口设计",
    ),
    "frontend": AgentDefinition(
        code="frontend",
        name="Frontend Agent",
        adapter_type="mock",
        system_prompt="负责前端页面、交互和状态展示。",
        capabilities="页面设计、交互实现、接口联调",
    ),
    "reviewer": AgentDefinition(
        code="reviewer",
        name="Reviewer Agent",
        adapter_type="mock",
        system_prompt="负责检查方案质量、风险和测试覆盖。",
        capabilities="代码审查、风险识别、测试建议",
    ),
}


def parse_command_instruction(content: str, command: str) -> str | None:
    stripped_content = content.strip()
    if not stripped_content.startswith(command):
        return None

    instruction = stripped_content.removeprefix(command).strip()
    return instruction or "请处理这条消息。"


def parse_mock_instruction(content: str) -> str | None:
    return parse_command_instruction(content, MOCK_COMMAND)


def parse_qwen_instruction(content: str) -> str | None:
    return parse_command_instruction(content, QWEN_COMMAND)


def parse_orchestrator_goal(content: str) -> str | None:
    return parse_command_instruction(content, ORCHESTRATOR_COMMAND)


def get_or_create_agent(db: Session, code: str) -> Agent:
    agent = db.scalar(select(Agent).where(Agent.code == code))
    if agent is not None:
        return agent

    definition = AGENT_DEFINITIONS[code]
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
    if agent.adapter_type == "aliyun_qwen":
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
        status="PENDING",
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
        ("backend", f"围绕目标「{goal}」设计后端数据模型、接口和服务流程。"),
        ("frontend", f"围绕目标「{goal}」设计前端页面、交互状态和接口调用方式。"),
        ("reviewer", f"围绕目标「{goal}」审查整体方案，列出风险、边界情况和测试重点。"),
    ]


def create_orchestrator_tasks_from_message(
    db: Session,
    conversation: Conversation,
    content: str,
) -> tuple[Task, list[Task]] | None:
    goal = parse_orchestrator_goal(content)
    if goal is None:
        return None

    orchestrator_agent = get_or_create_agent(db, "orchestrator")
    parent_task = Task(
        conversation_id=conversation.id,
        agent_id=orchestrator_agent.id,
        status="PENDING",
        instruction=goal,
    )
    db.add(parent_task)
    db.commit()
    db.refresh(parent_task)

    child_tasks: list[Task] = []
    for agent_code, instruction in build_orchestrator_subtasks(goal):
        agent = get_or_create_agent(db, agent_code)
        child_task = Task(
            conversation_id=conversation.id,
            parent_task_id=parent_task.id,
            agent_id=agent.id,
            status="PENDING",
            instruction=instruction,
        )
        db.add(child_task)
        child_tasks.append(child_task)

    db.commit()
    for child_task in child_tasks:
        db.refresh(child_task)

    return parent_task, child_tasks


async def broadcast_task_event(task: Task, event_name: str) -> None:
    event = TaskEvent(event=event_name, data=TaskRead.model_validate(task))
    await websocket_manager.broadcast_json(task.conversation_id, jsonable_encoder(event))


async def broadcast_agent_message(message: Message) -> None:
    event = WebSocketMessageEvent(data=MessageRead.model_validate(message))
    await websocket_manager.broadcast_json(message.conversation_id, jsonable_encoder(event))


async def run_single_agent_task(task_id: int, create_reply_message: bool = True) -> None:
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return

        task.status = "RUNNING"
        db.commit()
        db.refresh(task)
        await broadcast_task_event(task, "task.updated")

        agent = db.get(Agent, task.agent_id)
        if agent is None:
            raise RuntimeError("任务关联的 Agent 不存在。")

        adapter = get_adapter(agent)
        result = await adapter.run(
            AgentRunRequest(
                task_id=task.id,
                conversation_id=task.conversation_id,
                instruction=task.instruction,
                context={"system_prompt": agent.system_prompt or ""},
            )
        )

        task.status = "SUCCESS" if result.status == "success" else "FAILED"
        task.result_summary = result.summary
        db.commit()
        db.refresh(task)
        await broadcast_task_event(task, "task.updated")

        if create_reply_message:
            agent_message = Message(
                conversation_id=task.conversation_id,
                sender_type="agent",
                sender_id=task.agent_id,
                content=result.summary,
                message_type="text",
            )
            db.add(agent_message)
            db.commit()
            db.refresh(agent_message)
            await broadcast_agent_message(agent_message)
    except Exception as exc:
        task = db.get(Task, task_id)
        if task is not None:
            task.status = "FAILED"
            task.error_message = str(exc)
            db.commit()
            db.refresh(task)
            await broadcast_task_event(task, "task.updated")
    finally:
        db.close()


async def run_mock_agent_task(task_id: int) -> None:
    await run_single_agent_task(task_id, create_reply_message=True)


async def run_qwen_agent_task(task_id: int) -> None:
    await run_single_agent_task(task_id, create_reply_message=True)


async def run_orchestrator_task(parent_task_id: int) -> None:
    db = SessionLocal()
    try:
        parent_task = db.get(Task, parent_task_id)
        if parent_task is None:
            return

        parent_task.status = "RUNNING"
        db.commit()
        db.refresh(parent_task)
        await broadcast_task_event(parent_task, "task.updated")

        child_ids = list(
            db.scalars(
                select(Task.id)
                .where(Task.parent_task_id == parent_task.id)
                .order_by(Task.id.asc())
            )
        )
    finally:
        db.close()

    for child_id in child_ids:
        await run_single_agent_task(child_id, create_reply_message=True)

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
        failed_tasks = [task for task in child_tasks if task.status == "FAILED"]
        if failed_tasks:
            parent_task.status = "FAILED"
            parent_task.error_message = "存在子任务执行失败。"
        else:
            parent_task.status = "SUCCESS"
            parent_task.result_summary = build_orchestrator_summary(parent_task, child_tasks)

        db.commit()
        db.refresh(parent_task)
        await broadcast_task_event(parent_task, "task.updated")

        if parent_task.result_summary:
            summary_message = Message(
                conversation_id=parent_task.conversation_id,
                sender_type="agent",
                sender_id=parent_task.agent_id,
                content=parent_task.result_summary,
                message_type="text",
            )
            db.add(summary_message)
            db.commit()
            db.refresh(summary_message)
            await broadcast_agent_message(summary_message)
    finally:
        db.close()


def build_orchestrator_summary(parent_task: Task, child_tasks: list[Task]) -> str:
    lines = [f"Orchestrator 已完成任务拆解与执行：{parent_task.instruction}"]
    for index, task in enumerate(child_tasks, start=1):
        lines.append(f"{index}. 子任务 {task.id}：{task.result_summary or task.instruction}")
    return "\n".join(lines)

