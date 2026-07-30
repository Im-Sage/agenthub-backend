from celery import Celery
from app.core.config import settings

# 初始化 Celery 实例
# broker: 消息中间件（Redis）
# backend: 结果存储（Redis）
celery_app = Celery(
    "agenthub",
    # Celery Worker监听 Redis，确保 Celery Worker 能够接收任务消息
    broker=settings.redis_url,
    backend=settings.redis_url,
    # 显式包含任务模块，确保 Worker 启动时能加载它们
    include=[
        "app.workers.agent_tasks",
        "app.workers.index_tasks",
    ],
)


# 常用配置
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务失败后不自动重试（后续可按需配置）
    # task_acks_late=True,
    #   Worker 收到任务
    #   → 不立即 ACK
    #   → 执行 run_agent_task()
    #   → 任务执行完成
    #   → 再 ACK
    task_acks_late=True,
    # 单个 worker 预取任务数
    worker_prefetch_multiplier=1,
    # Soft Time Limit 和 Hard Time Limit 都是 Celery 用来限制单个任务最大执行时间的，但处理方式不同。
    # Soft Time Limit: 当任务执行时间超过这个限制时，Celery 会发送一个 Soft Time Limit 信号给任务，任务可以捕获这个信号并进行清理操作，然后优雅地退出。
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    # Hard Time Limit: 当任务执行时间超过这个限制时，Celery 会强制终止任务，不管任务是否完成清理操作。
    task_time_limit=settings.task_time_limit_seconds,
)
