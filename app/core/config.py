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

    aliyun_api_key: str | None = None
    aliyun_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    aliyun_model: str = "qwen-plus"
    aliyun_timeout_seconds: float = 120.0

    github_token: str | None = None

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8")

    @property
    def resolved_database_url(self) -> str:
        """把 SQLite 相对路径固定到项目根目录，避免从不同目录启动时连错数据库。"""
        if not self.database_url.startswith("sqlite:///./"):
            return self.database_url

        relative_path = self.database_url.replace("sqlite:///./", "", 1)
        database_path = PROJECT_ROOT / relative_path
        return f"sqlite:///{database_path.as_posix()}"


settings = Settings()

