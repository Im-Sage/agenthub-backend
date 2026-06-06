from langgraph.graph import StateGraph, START, END

from app.agents.graph.nodes import plan_node, execute_node, verify_node, summarize_node
from app.agents.graph.state import AgentState


def create_agent_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("planner", plan_node)
    workflow.add_node("executor", execute_node)
    workflow.add_node("verifier", verify_node)
    workflow.add_node("summarizer", summarize_node)

    workflow.add_edge(START, "planner")

    def planner_router(state: AgentState):
        if state.get("awaiting_confirmation"):
            return END
        return "executor"

    workflow.add_conditional_edges(
        "planner",
        planner_router,
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
    return workflow.compile()


agent_graph = create_agent_graph()
