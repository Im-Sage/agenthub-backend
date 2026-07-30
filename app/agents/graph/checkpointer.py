import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.config import settings


_postgres_setup_lock = asyncio.Lock()
_postgres_setup_complete = False


def _postgres_dsn() -> str:
    dsn = settings.langgraph_checkpoint_database_url
    if not dsn:
        raise RuntimeError(
            "langgraph_checkpoint_database_url is required "
            "for the postgres checkpoint backend"
        )
    return dsn


async def ensure_postgres_checkpointer_schema() -> None:
    global _postgres_setup_complete

    if (
        settings.langgraph_checkpoint_backend != "postgres"
        or not settings.langgraph_checkpoint_auto_setup
        or _postgres_setup_complete
    ):
        return

    async with _postgres_setup_lock:
        if _postgres_setup_complete:
            return
        async with AsyncPostgresSaver.from_conn_string(
            _postgres_dsn()
        ) as checkpointer:
            await checkpointer.setup()
        _postgres_setup_complete = True


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[
    AsyncSqliteSaver | AsyncPostgresSaver
]:
    if settings.langgraph_checkpoint_backend == "sqlite":
        checkpoint_path = Path(settings.resolved_langgraph_checkpoint_path)
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(
            checkpoint_path.as_posix()
        ) as checkpointer:
            yield checkpointer
        return

    if settings.langgraph_checkpoint_backend == "postgres":
        await ensure_postgres_checkpointer_schema()
        async with AsyncPostgresSaver.from_conn_string(
            _postgres_dsn()
        ) as checkpointer:
            yield checkpointer
        return

    raise RuntimeError(
        "Unsupported LangGraph checkpoint backend: "
        f"{settings.langgraph_checkpoint_backend}"
    )
