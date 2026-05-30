from celery import Celery
from app.core.config import settings

# 初始化 Celery 实例
# broker: 消息中间件（Redis）
# backend: 结果存储（Redis）
celery_app = Celery(
    "agenthub",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # 显式包含任务模块，确保 Worker 启动时能加载它们
    include=["app.workers.agent_tasks"],
)


# 常用配置
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务失败后不自动重试（后续可按需配置）
    task_acks_late=True,
    # 单个 worker 预取任务数
    worker_prefetch_multiplier=1,
)
