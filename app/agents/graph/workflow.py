from langgraph.graph import StateGraph, START, END

from app.agents.graph.nodes import (
    approval_node,
    execute_node,
    plan_node,
    summarize_node,
    verify_node,
)
from app.agents.graph.state import AgentState


def create_agent_graph(checkpointer=None):
    # 创建一个状态图，定义节点和边
    workflow = StateGraph(AgentState)

    # add_node的参数是节点的名称和节点对象，add_edge的参数是起始节点和目标节点
    workflow.add_node("planner", plan_node)
    workflow.add_node("approval", approval_node)
    workflow.add_node("executor", execute_node)
    workflow.add_node("verifier", verify_node)
    workflow.add_node("summarizer", summarize_node)

    # 添加边，定义节点之间的流转关系
    # START是图的起始节点，END是图的结束节点
    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "approval")

    def approval_router(state: AgentState):
        if state.get("approval_status") == "approved":
            return "executor"
        return END

    workflow.add_conditional_edges(
        "approval",
        approval_router,
        {
            "executor": "executor",
            END: END,
        },
    )
    workflow.add_edge("executor", "verifier")

    def router(state: AgentState):
        if state.get("is_finished"):
            return "summarizer"
        if state.get("errors"):
            return "executor"
        return "executor"

    workflow.add_conditional_edges(
        "verifier",
        router,
        {
            "executor": "executor",
            "summarizer": "summarizer",
        },
    )

    workflow.add_edge("summarizer", END)
    return workflow.compile(checkpointer=checkpointer)
