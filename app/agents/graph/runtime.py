from contextlib import asynccontextmanager

from app.agents.graph.checkpointer import open_checkpointer
from app.agents.graph.workflow import create_agent_graph

def graph_thread_id(task_id: int) -> str:
    """
    根据任务ID生成一个唯一的线程ID，用于在LangGraph中区分不同的任务执行上下文。
    这个线程ID可以用于在LangGraph的状态图中跟踪和管理不同任务的执行状态。
    """
    return f"orchestrator-task-{task_id}"

def graph_config(task_id: int) -> dict:
    """
    根据任务ID生成一个配置字典，用于在LangGraph中配置状态图的执行上下文。
    这个配置字典包含一个"configurable"键，其值是一个包含线程ID的字典，用于在LangGraph中区分不同任务的执行上下文。
    """
    return {
        "configurable":{
            "thread_id": graph_thread_id(task_id)
        }
    }


# open_agent_graph 是一个异步上下文管理器，用于创建和管理 LangGraph 的状态图，并提供一个检查点保存器。
@asynccontextmanager
async def open_agent_graph():
    async with open_checkpointer() as checkpointer:
        yield create_agent_graph(checkpointer=checkpointer)
