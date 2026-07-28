import asyncio

from app.core.logging import get_logger
from app.rag.index_service import RepositoryIndexService
from app.workers.celery_app import celery_app


logger = get_logger("worker.index_tasks")


@celery_app.task(
    bind=True,
    name="app.workers.index_tasks.index_repository_task",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def index_repository_task(self, repository_id: int):
    try:
        summary = asyncio.run(
            RepositoryIndexService().index_repository(repository_id)
        )
        return summary.model_dump()
    except Exception:
        logger.exception(
            "repository_index_failed repository_id=%s",
            repository_id,
        )
        raise


@celery_app.task(
    bind=True,
    name="app.workers.index_tasks.update_repository_files_task",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def update_repository_files_task(
    self,
    repository_id: int,
    file_paths: list[str],
):
    try:
        summary = asyncio.run(
            RepositoryIndexService().update_files(
                repository_id,
                file_paths,
            )
        )
        return summary.model_dump()
    except Exception:
        logger.exception(
            "repository_incremental_index_failed repository_id=%s",
            repository_id,
        )
        raise
