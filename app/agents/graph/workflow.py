from langgraph.graph import StateGraph, START, END

from app.agents.graph.nodes import (
    approval_node,
    dispatch_node,
    plan_node,
    reject_plan_node,
)
from app.agents.graph.state import AgentState


def create_agent_graph(checkpointer=None):
    # 创建一个状态图，定义节点和边
    workflow = StateGraph(AgentState)

    # add_node的参数是节点的名称和节点对象，add_edge的参数是起始节点和目标节点
    workflow.add_node("planner", plan_node)
    workflow.add_node("approval", approval_node)
    workflow.add_node("dispatcher", dispatch_node)
    workflow.add_node("reject_plan", reject_plan_node)

    # 添加边，定义节点之间的流转关系
    # START是图的起始节点，END是图的结束节点
    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "approval")

    def approval_router(state: AgentState):
        if state.get("approval_status") == "approved":
            return "dispatcher"
        return "reject_plan"

    workflow.add_conditional_edges(
        "approval",
        approval_router,
        {
            "dispatcher": "dispatcher",
            "reject_plan": "reject_plan",
        },
    )
    workflow.add_edge("dispatcher", END)
    workflow.add_edge("reject_plan", END)
    return workflow.compile(checkpointer=checkpointer)
