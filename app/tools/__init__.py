from app.tools.command_tools import register_command_tools
from app.tools.workspace_tools import register_workspace_tools


def register_builtin_tools() -> None:
    register_workspace_tools()
    register_command_tools()
