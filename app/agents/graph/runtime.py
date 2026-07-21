from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agents.graph.workflow import create_agent_graph
from app.core.config import settings

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
    # 确保检查点路径存在，如果不存在则创建父目录
    checkpoint_path = Path(settings.resolved_langgraph_checkpoint_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    # 使用 AsyncSqliteSaver 从检查点路径创建一个异步 SQLite 保存器，并在上下文中提供创建的状态图
    # AsyncSqliteSaver是一个异步的SQLite检查点保存器，用于在LangGraph中保存和恢复状态图每一步的执行状态。
    async with AsyncSqliteSaver.from_conn_string(
        checkpoint_path.as_posix() # 将 Path 对象转换为字符串路径，正斜杠形式
    ) as checkpointer:
        # 返回的不再是单纯的内存 Graph，而是一个会把运行状态写入 SQLite 的 Compiled Graph
        yield create_agent_graph(checkpointer=checkpointer)
