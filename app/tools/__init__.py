from app.tools.command_tools import register_command_tools
from app.tools.rag_tools import register_rag_tools
from app.tools.workspace_tools import register_workspace_tools


def register_builtin_tools() -> None:
    register_workspace_tools()
    register_command_tools()
    register_rag_tools()
