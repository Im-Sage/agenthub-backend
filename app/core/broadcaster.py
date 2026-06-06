import asyncio
import json
from typing import Any

import redis.asyncio as redis

from app.core.config import settings
from app.core.logging import get_logger


logger = get_logger("broadcaster")


class Broadcaster:
    def __init__(self):
        self._redis_client = None
        self.pubsub = None
        self._stop_event = asyncio.Event()
        self._listen_task = None

    def get_redis(self):
        if self._redis_client is None:
            self._redis_client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                max_connections=20,
                socket_timeout=10.0,
                health_check_interval=30,
            )
        return self._redis_client

    async def publish(self, channel: str, message: Any):
        logger.info("publish channel=%s event=%s", channel, message.get("event", "message"))
        client = self.get_redis()
        try:
            await client.publish(channel, json.dumps(message))
        except Exception as exc:
            logger.exception("publish_failed channel=%s error=%s", channel, exc)

    async def subscribe(self, channel_pattern: str, callback):
        client = self.get_redis()
        self.pubsub = client.pubsub()

        logger.info("subscribe channel_pattern=%s", channel_pattern)
        await self.pubsub.psubscribe(channel_pattern)

        async def listen():
            logger.info("listen_loop_started")
            while not self._stop_event.is_set():
                try:
                    async for message in self.pubsub.listen():
                        if message["type"] == "pmessage":
                            data = json.loads(message["data"])
                            channel_name = message["channel"]
                            conv_id = channel_name.split("_")[-1]
                            await callback(int(conv_id), data)
                except redis.TimeoutError:
                    continue
                except redis.ConnectionError as exc:
                    logger.warning("redis_connection_lost error=%s retry_in_seconds=2", exc)
                    await asyncio.sleep(2)
                    try:
                        await self.pubsub.psubscribe(channel_pattern)
                    except Exception:
                        logger.exception("resubscribe_failed channel_pattern=%s", channel_pattern)
                except Exception as exc:
                    if self._stop_event.is_set():
                        break
                    logger.exception("listen_loop_unexpected_error error=%s", exc)
                    await asyncio.sleep(5)

        self._listen_task = asyncio.create_task(listen())

    async def stop(self):
        self._stop_event.set()
        if self._listen_task:
            self._listen_task.cancel()
        if self.pubsub:
            await self.pubsub.punsubscribe()
        if self._redis_client:
            await self._redis_client.close()


broadcaster = Broadcaster()
