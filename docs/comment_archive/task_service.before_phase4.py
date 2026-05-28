from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import AgentRunRequest
from app.agents.mock_adapter import MockAgentAdapter
from app.core.websocket_manager import websocket_manager
from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.task import Task
from app.schemas.message import MessageRead, WebSocketMessageEvent
from app.schemas.task import TaskEvent, TaskRead


MOCK_COMMAND = "@mock"

"""
这个模块的设计主要是为了实现一个 Mock Agent 的功能，允许用户通过特定的消息格式触发一个模拟任务，并通过 WebSocket 实时推送任务状态和结果更新。以下是该模块的主要功能和设计考虑：
1. 指令解析：通过 parse_mock_instruction 函数解析用户消息，识别以 @mock 开头的消息，并提取指令内容。如果消息不符合格式，则返回 None，表示不需要创建 Mock 任务。
2. Mock Agent 管理：通过 get_or_create_mock_agent 函数确保数据库中存在一个 Mock Agent 实例，如果不存在则创建一个新的实例。这个 Agent 用于处理所有 Mock 任务，具有固定的名称、代码和适配器类型。
3. 任务创建：通过 create_mock_task_from_message 函数在用户发送符合 @mock 指令格式的消息时创建一个新的 Task 实例，关联到对应的 Conversation 和 Mock Agent，并设置初始状态为 PENDING。
4. 任务执行：通过 run_mock_agent_task 函数执行 Mock 任务，更新任务状态为 RUNNING，并使用 MockAgentAdapter 处理任务指令。根据适配器的执行结果，更新任务状态为 SUCCESS 或 FAILED，并将结果摘要保存到数据库。
5. 实时推送：在任务状态更新和结果生成后，通过 WebSocketManager 向所有连接到该 Conversation 的客户端广播任务更新事件和新消息事件，实现实时反馈用户任务执行情况和结果。
6. 错误处理：在任务执行过程中捕获异常，如果发生错误，则将任务状态更新为 FAILED，并保存错误信息到数据库，同时通过 WebSocket 推送任务更新事件告知客户端任务执行失败。
总的来说，这个模块的设计旨在提供一个完整的 Mock Agent 功能，从指令解析、任务管理、执行到实时反馈，确保用户能够通过简单的消息格式触发模拟任务，并实时了解任务的执行状态和结果，同时通过数据库持久化任务信息和结果，便于后续查询和分析。  
"""
# 解析用户消息，识别以 @mock 开头的消息，并提取指令内容。如果消息不符合格式，则返回 None，表示不需要创建 Mock 任务。
def parse_mock_instruction(content: str) -> str | None:
    stripped_content = content.strip()
    if not stripped_content.startswith(MOCK_COMMAND):
        return None

    instruction = stripped_content.removeprefix(MOCK_COMMAND).strip()
    return instruction or "请 Mock Agent 处理这条消息。"

# 通过 get_or_create_mock_agent 函数确保数据库中存在一个 Mock Agent 实例，
# 如果不存在则创建一个新的实例。这个 Agent 用于处理所有 Mock 任务，
# 具有固定的名称、代码和适配器类型。
def get_or_create_mock_agent(db: Session) -> Agent:
    agent = db.scalar(select(Agent).where(Agent.code == "mock"))
    if agent is not None:
        return agent

    agent = Agent(
        name="Mock Agent",
        code="mock",
        adapter_type="mock",
        system_prompt="用于第三阶段联调的模拟 Agent。",
        capabilities="任务回显、流程联调、WebSocket 推送验证",
        enabled=True,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent

# 通过 create_mock_task_from_message 函数在用户发送符合 @mock 指令格式的消息时创建一个新的 Task 实例，
# 关联到对应的 Conversation 和 Mock Agent，并设置初始状态为 PENDING。
def create_mock_task_from_message(db: Session, conversation: Conversation, content: str) -> Task | None:
    instruction = parse_mock_instruction(content)
    if instruction is None:
        return None

    agent = get_or_create_mock_agent(db)
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


# 在任务状态更新和结果生成后，通过 WebSocketManager 向所有连接到该 Conversation 的客户端
# 广播任务更新事件和新消息事件，实现实时反馈用户任务执行情况和结果。
async def broadcast_task_event(task: Task, event_name: str) -> None:
    event = TaskEvent(event=event_name, data=TaskRead.model_validate(task))
    await websocket_manager.broadcast_json(task.conversation_id, jsonable_encoder(event))


# 通过 run_mock_agent_task 函数执行 Mock 任务，更新任务状态为 RUNNING，并使用 MockAgentAdapter 处理任务指令。
# 根据适配器的执行结果，更新任务状态为 SUCCESS 或 FAILED，并将结果摘要保存到数据库。
async def run_mock_agent_task(task_id: int) -> None:
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return

        task.status = "RUNNING"
        db.commit()
        db.refresh(task)
        await broadcast_task_event(task, "task.updated")

        adapter = MockAgentAdapter()
        result = await adapter.run(
            AgentRunRequest(
                task_id=task.id,
                conversation_id=task.conversation_id,
                instruction=task.instruction,
            )
        )

        task.status = "SUCCESS" if result.status == "success" else "FAILED"
        task.result_summary = result.summary
        db.commit()
        db.refresh(task)
        await broadcast_task_event(task, "task.updated")

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

        message_event = WebSocketMessageEvent(data=MessageRead.model_validate(agent_message))
        await websocket_manager.broadcast_json(task.conversation_id, jsonable_encoder(message_event))
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

