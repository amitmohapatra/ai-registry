"""Event bus strategies (Observer pattern).

InMemoryBus for single-process / tests; RedisBus when a product has Redis configured.
The registry publishes to the product's own channel: registry:{product_key}.
"""
import asyncio
import json
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import AsyncIterator


class EventBus(ABC):
    @abstractmethod
    async def publish(self, channel: str, message: dict) -> None: ...

    @abstractmethod
    def subscribe(self, channel: str) -> AsyncIterator[dict]: ...


class InMemoryBus(EventBus):
    def __init__(self):
        self._subs: dict = defaultdict(list)

    async def publish(self, channel: str, message: dict) -> None:
        for q in list(self._subs.get(channel, [])):
            q.put_nowait(message)

    async def subscribe(self, channel: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[channel].append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs[channel].remove(q)


class RedisBus(EventBus):
    """Lazy Redis-backed bus; one instance per distinct redis URL."""

    def __init__(self, url: str):
        import redis.asyncio as aioredis  # imported only when actually used
        self._redis = aioredis.from_url(url, decode_responses=True)

    async def publish(self, channel: str, message: dict) -> None:
        await self._redis.publish(channel, json.dumps(message))

    async def subscribe(self, channel: str) -> AsyncIterator[dict]:
        async with self._redis.pubsub() as ps:
            await ps.subscribe(channel)
            async for msg in ps.listen():
                if msg["type"] == "message":
                    yield json.loads(msg["data"])


class BusRouter:
    """Publishes each product's events to its own bus (per-product Redis), with an
    in-memory default. Failures to reach a product's Redis never fail the write path."""

    def __init__(self):
        from .config import get_settings
        url = get_settings().redis_url
        try:
            self.default = RedisBus(url) if url else InMemoryBus()
        except Exception:
            self.default = InMemoryBus()
        self._by_url: dict = {}

    def bus_for(self, redis_url: str = "") -> EventBus:
        if not redis_url:
            return self.default
        if redis_url not in self._by_url:
            try:
                self._by_url[redis_url] = RedisBus(redis_url)
            except Exception:
                return self.default
        return self._by_url[redis_url]

    async def publish(self, channel: str, message: dict, redis_url: str = "") -> None:
        try:
            await self.bus_for(redis_url).publish(channel, message)
        except Exception:
            pass  # control-plane publish must never break the write transaction result
        if redis_url:  # mirror on the in-process bus so local subscribers (tests/UI SSE) see it too
            await self.default.publish(channel, message)


bus_router = BusRouter()
