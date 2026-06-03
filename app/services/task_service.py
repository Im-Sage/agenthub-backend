from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import AgentAdapter, AgentRunRequest
from app.agents.mock_adapter import MockAgentAdapter
from app.agents.qwen_adapter import QwenAgentAdapter
from app.core.broadcaster import broadcaster
from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.task import Task
from app.schemas.enums import TaskStatus, MessageType, SenderType, AgentAdapterType
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
        adapter_type="qwen",
        system_prompt="使用阿里通义千问处理真实 LLM任务。",
        capabilities="真实 LLM 回复、需求分析、方案生成",
    ),
    "orchestrator": AgentDefinition(
        code="orchestrator",
        name="Orchestrator Agent",
        adapter_type="qwen",
        system_prompt=(
            "你是一个专业的软件工程任务拆解专家。你的任务是根据用户目标，将其拆解为多个子任务。\n"
            "【强制规则】\n"
            "1. 必须且只能输出一个标准的 JSON 数组。\n"
            "2. 数组中的对象必须包含 'agent' (值只能是 'backend', 'frontend', 'reviewer') 和 'instruction' (详细指令) 两个字段。\n"
            "3. 禁止输出任何开场白、解释说明、Markdown 列表或结论。\n\n"
            "【输出示例】\n"
            "[\n"
            "  {\"agent\": \"backend\", \"instruction\": \"创建数据模型\"},\n"
            "  {\"agent\": \"reviewer\", \"instruction\": \"代码审查\"}\n"
            "]"
        ),
        capabilities="需求分析、任务拆解、Agent 分派",
    ),
    "backend": AgentDefinition(
        code="backend",
        name="Backend Agent",
        adapter_type="qwen",
        system_prompt="负责后端接口、数据模型和服务逻辑。",
        capabilities="FastAPI、SQLAlchemy、接口设计",
    ),
    "frontend": AgentDefinition(
        code="frontend",
        name="Frontend Agent",
        adapter_type="qwen",
        system_prompt="负责前端页面、交互和状态展示。",
        capabilities="页面设计、交互实现、接口联调",
    ),
    "reviewer": AgentDefinition(
        code="reviewer",
        name="Reviewer Agent",
        adapter_type="qwen",
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
    definition = AGENT_DEFINITIONS[code]
    
    if agent is not None:
        # 强制更新已存在 Agent 的配置，确保配置与代码同步
        if agent.adapter_type != definition.adapter_type or agent.system_prompt != definition.system_prompt:
            agent.adapter_type = definition.adapter_type
            agent.system_prompt = definition.system_prompt
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
    if agent.adapter_type in ["aliyun_qwen", "qwen"]:
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
        ("backend", f"围绕目标「{goal}」设计后端数据模型、接口和服务流程。"),
        ("frontend", f"围绕目标「{goal}」设计前端页面、交互状态和接口调用方式。"),
        ("reviewer", f"围绕目标「{goal}」审查整体方案，列出风险、边界情况和测试重点。"),
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
    )
    db.add(parent_task)
    db.commit()
    db.refresh(parent_task)

    return parent_task


def create_subtask(
    db: Session, 
    parent_task: Task, 
    agent_code: str, 
    instruction: str,
    task_type: str | None = None,
    depends_on: list[int] | None = None
) -> Task:
    agent = get_or_create_agent(db, agent_code)
    child_task = Task(
        conversation_id=parent_task.conversation_id,
        parent_task_id=parent_task.id,
        agent_id=agent.id,
        status=TaskStatus.PENDING,
        instruction=instruction,
        task_type=task_type,
        depends_on=json.dumps(depends_on) if depends_on else None
    )
    db.add(child_task)
    db.commit()
    db.refresh(child_task)
    return child_task


async def broadcast_task_event(task: Task, event_name: str) -> None:
    print(f"[TaskService] Broadcasting event: {event_name} for task {task.id}")
    event = TaskEvent(event=event_name, data=TaskRead.model_validate(task))
    await broadcaster.publish(f"conv_{task.conversation_id}", jsonable_encoder(event))


async def broadcast_task_log(task: Task, message: str) -> None:
    """发送实时执行日志给前端"""
    log_event = {
        "event": "task.log",
        "data": {
            "task_id": task.id,
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
    }
    await broadcaster.publish(f"conv_{task.conversation_id}", log_event)


async def broadcast_agent_message(message: Message) -> None:
    event = WebSocketMessageEvent(data=MessageRead.model_validate(message))
    await broadcaster.publish(f"conv_{message.conversation_id}", jsonable_encoder(event))


def build_orchestrator_summary(parent_task: Task, child_tasks: list[Task]) -> str:
    lines = [f"Orchestrator 已完成任务拆解与执行：{parent_task.instruction}"]
    for index, task in enumerate(child_tasks, start=1):
        lines.append(f"{index}. 子任务 {task.id}：{task.result_summary or task.instruction}")
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")

    get_owned_conversation(db, task.conversation_id, user_id)

    return task


def list_child_tasks(db: Session, user_id: int, task_id: int) -> list[Task]:
    parent_task = get_task(db, user_id, task_id)
    statement = select(Task).where(Task.parent_task_id == parent_task.id).order_by(Task.id.asc())
    return list(db.scalars(statement))

