from pathlib import Path

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

    github_token: str | None = None
    
    # MCP Settings
    mcp_enabled: bool = False
    mcp_tool_mode: str = "local"  # local | mcp | hybrid
    mcp_workspace_server_url: str | None = None
    mcp_internal_token: str | None = None

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8")

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


settings = Settings()

