from app.mcp.repository_resolver import WorkspaceAuthorizationError
from app.rag.retrieval import HybridCodeRetriever
from app.tools.base import (
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
    ToolRiskLevel,
)
from app.tools.registry import ToolRegistry, tool_registry


hybrid_code_retriever = HybridCodeRetriever()


async def workspace_semantic_search(
    request: ToolCallRequest,
) -> ToolCallResult:
    if request.repository_id is None or request.user_id is None:
        return ToolCallResult(
            success=False,
            error="Trusted repository_id and user_id are required",
        )
    query = request.arguments.get("query")
    top_k = request.arguments.get("top_k", 8)
    try:
        results = await hybrid_code_retriever.search(
            repository_id=request.repository_id,
            user_id=request.user_id,
            query=query if isinstance(query, str) else "",
            top_k=top_k,
        )
    except (ValueError, WorkspaceAuthorizationError) as exc:
        return ToolCallResult(success=False, error=str(exc))
    return ToolCallResult(
        success=True,
        content=f"Retrieved {len(results)} code chunks.",
        structured_content={
            "results": [result.model_dump() for result in results]
        },
    )


def register_rag_tools(
    registry: ToolRegistry = tool_registry,
) -> None:
    registry.register(
        ToolDefinition(
            name="workspace.semantic_search",
            description=(
                "Search repository code using keyword and semantic "
                "similarity."
            ),
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Semantic code search query.",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 8,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        workspace_semantic_search,
    )
