from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "AgentHub Backend"
    app_env: str = "dev"
    secret_key: str = "agenthub-dev-secret-key"
    access_token_expire_minutes: int = 60 * 24
    database_url: str = "sqlite:///./agenthub.db"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    cors_allow_origin_regex: str = (
        r"^http://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|"
        r"192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+):5173$"
    )
    login_rate_limit_count: int = 5
    login_rate_limit_window_seconds: int = 300
    message_rate_limit_count: int = 20
    message_rate_limit_window_seconds: int = 60
    max_concurrent_tasks_per_user: int = 20
    task_soft_time_limit_seconds: int = 300
    task_time_limit_seconds: int = 360
    max_agent_file_bytes: int = 500_000
    # LangGraph 相关配置，用于存储 LangGraph 的检查点数据，确保在不同环境下启动时能够正确访问数据库。
    langgraph_checkpoint_path: str = "./langgraph_checkpoints.sqlite3"
    langgraph_checkpoint_backend: Literal["sqlite", "postgres"] = "sqlite"
    langgraph_checkpoint_database_url: str | None = None
    langgraph_checkpoint_auto_setup: bool = True

    agent_worktree_root: str = "./task_worktrees"
    repository_git_lock_timeout_seconds: int = 30
    repository_git_lock_ttl_seconds: int = 120
    orchestrator_step_max_repair_attempts: int = 2
    orchestrator_step_max_retries: int = 2

    aliyun_api_key: str | None = None
    aliyun_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    aliyun_model: str = "qwen-plus"
    aliyun_timeout_seconds: float = 120.0
    agent_tool_max_rounds: int = 8
    agent_legacy_file_protocol_fallback: bool = False
    agent_command_timeout_seconds: int = 120
    agent_command_max_output_chars: int = 50_000
    agent_command_allowed_env: str = (
        "PATH,PYTHONPATH,HOME,USERPROFILE,TEMP,TMP,SYSTEMROOT,COMSPEC"
    )
    embedding_provider: str = "hash"
    embedding_model: str = "text-embedding-v4"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_dimensions: int = 256
    rag_chunk_batch_size: int = 32
    agent_context_max_tokens: int = 24_000
    agent_context_system_tokens: int = 3_000
    agent_context_conversation_tokens: int = 4_000
    agent_context_retrieval_tokens: int = 10_000
    agent_context_execution_tokens: int = 5_000
    agent_context_response_reserve_tokens: int = 2_000
    agent_context_max_retrieval_chunks: int = 8

    github_token: str | None = None
    
    # MCP Settings
    mcp_enabled: bool = False
    mcp_tool_mode: str = "local"  # local | mcp | hybrid
    mcp_workspace_server_url: str | None = None
    mcp_internal_token: str | None = None
    mcp_dynamic_discovery_enabled: bool = True
    mcp_dynamic_server_id: str = "workspace"
    mcp_dynamic_namespace: str = "mcp.workspace"
    mcp_dynamic_fail_closed: bool = False
    mcp_dynamic_allowlist: str = (
        "workspace_read_file,workspace_list_files,workspace_search_code,"
        "workspace_write_file,workspace_rename_file,"
        "workspace_get_diff,workspace_get_changed_files"
    )
    mcp_dynamic_denylist: str = "workspace_delete_file"
    mcp_dynamic_medium_risk_tools: str = (
        "workspace_write_file,workspace_rename_file"
    )
    mcp_dynamic_agent_profiles_json: str = "{}"

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def validate_context_budgets(self):
        allocated = (
            self.agent_context_system_tokens
            + self.agent_context_conversation_tokens
            + self.agent_context_retrieval_tokens
            + self.agent_context_execution_tokens
            + self.agent_context_response_reserve_tokens
        )
        if allocated > self.agent_context_max_tokens:
            raise ValueError(
                "Agent context category budgets and response reserve "
                "must not exceed agent_context_max_tokens"
            )
        return self

    @model_validator(mode="after")
    def validate_checkpoint_configuration(self):
        if (
            self.langgraph_checkpoint_backend == "postgres"
            and not self.langgraph_checkpoint_database_url
        ):
            raise ValueError(
                "langgraph_checkpoint_database_url is required "
                "when langgraph_checkpoint_backend is 'postgres'"
            )
        return self

    @property
    def resolved_database_url(self) -> str:
        """把 SQLite 相对路径固定到项目根目录，避免从不同目录启动时连错数据库。"""
        if not self.database_url.startswith("sqlite:///./"):
            return self.database_url

        relative_path = self.database_url.replace("sqlite:///./", "", 1)
        database_path = PROJECT_ROOT / relative_path
        return f"sqlite:///{database_path.as_posix()}"

    @property
    def resolved_langgraph_checkpoint_path(self) -> str:
        path = Path(self.langgraph_checkpoint_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve().as_posix()

    @property
    def resolved_agent_worktree_root(self) -> str:
        path = Path(self.agent_worktree_root)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve().as_posix()


settings = Settings()

