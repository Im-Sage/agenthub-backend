import asyncio
import json
from typing import Any
import redis.asyncio as redis
from app.core.config import settings

"""

"""
class Broadcaster:
    def __init__(self):
        self._redis_client = None
        self.pubsub = None
        self._stop_event = asyncio.Event()
        self._listen_task = None

    def get_redis(self):
        """延迟初始化 Redis 客户端连接池"""
        if self._redis_client is None:
            self._redis_client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                max_connections=20,  # 允许一定并发发布
                socket_timeout=10.0,
                health_check_interval=30
            )
        return self._redis_client

    async def publish(self, channel: str, message: Any):
        """发送消息到 Redis 频道"""
        print(f"[Broadcaster] Publishing to {channel}: {message.get('event', 'message')}")
        client = self.get_redis()
        try:
            await client.publish(channel, json.dumps(message))
        except Exception as e:
            print(f"[Broadcaster] Publish failed: {e}")

    async def subscribe(self, channel_pattern: str, callback):
        """订阅 Redis 频道并处理消息"""
        client = self.get_redis()
        self.pubsub = client.pubsub()

        print(f"[Broadcaster] Subscribing to pattern: {channel_pattern}")
        await self.pubsub.psubscribe(channel_pattern)
        
        async def listen():
            print("[Broadcaster] Listen loop started")
            while not self._stop_event.is_set():
                try:
                    async for message in self.pubsub.listen():
                        if message["type"] == "pmessage":
                            data = json.loads(message["data"])
                            channel_name = message["channel"]
                            conv_id = channel_name.split("_")[-1]
                            await callback(int(conv_id), data)
                except redis.TimeoutError:
                    # 正常的读取超时（10秒内没有新消息），静默继续即可
                    continue
                except redis.ConnectionError as e:
                    print(f"[Broadcaster] Redis connection lost: {e}. Retrying in 2s...")
                    await asyncio.sleep(2)
                    try:
                        # 尝试重新订阅
                        await self.pubsub.psubscribe(channel_pattern)
                    except Exception:
                        pass
                except Exception as e:
                    if self._stop_event.is_set(): break
                    print(f"[Broadcaster] Unexpected error: {e}")
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
