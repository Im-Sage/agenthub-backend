import time
from contextlib import contextmanager
from typing import Iterator
from uuid import uuid4

import redis

from app.core.config import settings


_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""
_RETRY_INTERVAL_SECONDS = 0.1
_redis_client = None


class RepositoryLockTimeout(RuntimeError):
    """Raised when a repository Git metadata lock cannot be acquired."""


def _get_redis_client():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=10.0,
            health_check_interval=30,
        )
    return _redis_client


@contextmanager
def repository_git_lock(repository_id: int) -> Iterator[None]:
    client = _get_redis_client()
    key = f"lock:repository-git:{repository_id}"
    token = uuid4().hex
    deadline = (
        time.monotonic()
        + settings.repository_git_lock_timeout_seconds
    )

    while not client.set(
        key,
        token,
        nx=True,
        ex=settings.repository_git_lock_ttl_seconds,
    ):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RepositoryLockTimeout(
                "Timed out acquiring Git metadata lock "
                f"for repository {repository_id}"
            )
        time.sleep(min(_RETRY_INTERVAL_SECONDS, remaining))

    try:
        yield
    finally:
        client.eval(_RELEASE_SCRIPT, 1, key, token)
