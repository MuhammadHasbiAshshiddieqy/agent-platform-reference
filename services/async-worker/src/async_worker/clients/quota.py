"""§6 L2 — reconciles the async-pool reservation gateway already made
before publishing this job. A deliberate, minimal COPY of
`services/gateway/src/gateway/quota.py`'s `_RECONCILE_SCRIPT`, not an
import of `QuotaManager` (§4.1 — services never import each other). Only
the reconcile half is needed here; reservation/sweeping stay gateway's
job entirely.
"""

from __future__ import annotations

from redis.asyncio import Redis

_RECONCILE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then
  return -1
end
local data = cjson.decode(raw)
local delta = tonumber(ARGV[1]) - tonumber(data['estimated'])
redis.call('INCRBY', data['quota_key'], delta)
redis.call('DEL', KEYS[1])
return delta
"""


class QuotaReconciler:
    def __init__(self, redis: Redis) -> None:
        self._reconcile_script = redis.register_script(_RECONCILE_SCRIPT)

    async def reconcile(self, reservation_key: str, actual_tokens: int) -> None:
        """Idempotent — a reservation gateway's own sweeper already
        reclaimed (job ran long past `async_job_deadline_seconds`) is
        silently a no-op here, same as gateway's own reconcile()."""
        await self._reconcile_script(keys=[reservation_key], args=[actual_tokens])
