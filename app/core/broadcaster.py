import asyncio
import json
from typing import Any
import redis.asyncio as redis
from app.core.config import settings

"""

"""
class Broadcaster:
    def __init__(self):
        self.redis_client = None
        self.pubsub = None
        self._stop_event = asyncio.Event()
        self._listen_task = None

    async def publish(self, channel: str, message: Any):
        """发送消息到 Redis 频道 (使用独立连接避免跨线程 loop 问题)"""
        print(f"[Broadcaster] Publishing to {channel}: {message.get('event', 'unknown')}")
        # 在 Celery 这种多线程多 loop 环境下，为了安全，每次发布使用新的短链接
        async with redis.from_url(settings.redis_url, decode_responses=True) as temp_client:
            await temp_client.publish(channel, json.dumps(message))

    async def subscribe(self, channel_pattern: str, callback):
        """订阅 Redis 频道并处理消息"""
        if self.redis_client is None:
            # 增加 health_check_interval 维持连接，增加超时时间
            self.redis_client = redis.from_url(
                settings.redis_url, 
                decode_responses=True,
                health_check_interval=30,
                socket_connect_timeout=5,
                socket_timeout=60
            )
            self.pubsub = self.redis_client.pubsub()

        print(f"[Broadcaster] Subscribing to pattern: {channel_pattern}")
        await self.pubsub.psubscribe(channel_pattern)
        
        async def listen():
            print("[Broadcaster] Listen loop started")
            while not self._stop_event.is_set():
                try:
                    # 持续迭代获取从 Redis 推送过来的消息
                    async for message in self.pubsub.listen():
                        if message["type"] == "pmessage":
                            print(f"[Broadcaster] Received pmessage on {message['channel']}")
                            data = json.loads(message["data"])
                            channel_name = message["channel"]
                            conv_id = channel_name.split("_")[-1]
                            await callback(int(conv_id), data)
                except redis.TimeoutError:
                    # 正常的读取超时，不做任何处理，继续循环即可
                    continue
                except redis.ConnectionError as e:
                    print(f"[Broadcaster] Connection issue: {e}. Retrying in 2s...")
                    await asyncio.sleep(2)
                    if self.pubsub:
                        try:
                            await self.pubsub.psubscribe(channel_pattern)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[Broadcaster] Listen error: {e}")
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(5)

        self._listen_task = asyncio.create_task(listen())

    async def stop(self):
        self._stop_event.set()
        if self._listen_task:
            self._listen_task.cancel()
        if self.pubsub:
            await self.pubsub.punsubscribe()
        if self.redis_client:
            await self.redis_client.close()

broadcaster = Broadcaster()
