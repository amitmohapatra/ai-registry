"""Two-tier cache (Strategy): in-process L1 with TTL always on; Redis L2 optional.
Invalidation is driven by the same publish that notifies SDKs."""
import contextlib
import json
import time
from abc import ABC, abstractmethod
from typing import Any, Optional


class Cache(ABC):
    @abstractmethod
    async def get(self, key: str) -> Optional[Any]: ...
    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int) -> None: ...
    @abstractmethod
    async def delete(self, key: str) -> None: ...


class InMemoryCache(Cache):
    def __init__(self):
        self._store: dict = {}

    async def get(self, key: str):
        item = self._store.get(key)
        if not item:
            return None
        value, expires = item
        if expires < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value, ttl: int):
        self._store[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str):
        self._store.pop(key, None)


class RedisCache(Cache):
    def __init__(self, url: str):
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(url, decode_responses=True)

    async def get(self, key: str):
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value, ttl: int):
        await self._redis.set(key, json.dumps(value), ex=ttl)

    async def delete(self, key: str):
        await self._redis.delete(key)


class TieredCache(Cache):
    """L1 in-memory in front of an optional L2. Any layer failing degrades silently."""

    def __init__(self, l2: Optional[Cache] = None):
        self.l1 = InMemoryCache()
        self.l2 = l2

    async def get(self, key: str):
        value = await self.l1.get(key)
        if value is not None:
            return value
        if self.l2:
            try:
                value = await self.l2.get(key)
            except Exception:
                value = None
            if value is not None:
                await self.l1.set(key, value, 60)
        return value

    async def set(self, key: str, value, ttl: int):
        await self.l1.set(key, value, ttl)
        if self.l2:
            with contextlib.suppress(Exception):
                await self.l2.set(key, value, ttl)

    async def delete(self, key: str):
        await self.l1.delete(key)
        if self.l2:
            with contextlib.suppress(Exception):
                await self.l2.delete(key)


def build_cache(redis_url: str = "") -> TieredCache:
    l2 = None
    if redis_url:
        try:
            l2 = RedisCache(redis_url)
        except Exception:
            l2 = None
    return TieredCache(l2)


def _default_cache() -> TieredCache:
    from .config import get_settings
    return build_cache(get_settings().redis_url)


cache = _default_cache()
