"""§22.6 — kill one tool or one agent in seconds without a deploy. Read
with a 10-second in-process TTL cache, exactly as the spec asks ("jangan
hit Redis per tool per request") — a killswitch flip takes up to 10s to
propagate, which is the deliberate cost of not hitting Redis on every
single tool-call check.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis

CACHE_TTL_SECONDS = 10.0


class KillswitchChecker:
    def __init__(self, redis: Redis, *, cache_ttl_seconds: float = CACHE_TTL_SECONDS) -> None:
        self._redis = redis
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[bool, float]] = {}

    async def _is_disabled(self, key: str) -> bool:
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and now - cached[1] < self._cache_ttl:
            return cached[0]
        value = await self._redis.get(key)
        disabled = value in (b"disabled", "disabled")
        self._cache[key] = (disabled, now)
        return disabled

    async def is_tool_disabled(self, tool_name: str) -> bool:
        return await self._is_disabled(f"killswitch:tool:{tool_name}")

    async def is_agent_disabled(self, agent_id: str) -> bool:
        return await self._is_disabled(f"killswitch:agent:{agent_id}")
