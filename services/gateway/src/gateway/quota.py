"""§6 L2 quota + §23.2a's reservation-leak hazard, both in one module: a
token-bucket-per-window counter (`quota:{tenant_id}:{pool}`), reserved
before a run and reconciled after, plus a sweeper that reclaims
reservations no one ever reconciled (crashed instance, killed request).

Every state transition is a single Redis Lua script (`EVAL`) — Redis runs
scripts atomically and single-threaded, so two concurrent reservations, or
a reconcile racing the sweeper for the same reservation, can never
interleave mid-operation. Whichever `EVAL` reaches Redis first completes
in full before the next one starts; the loser's `GET` on an
already-deleted reservation key is what makes both reconcile() and the
sweeper safely idempotent no-ops on their second attempt.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from prometheus_client import Counter
from redis.asyncio import Redis

quota_rejections_total = Counter(
    "quota_rejections_total", "§12.2 — L2 quota reservation rejected", ["tenant_id", "pool"]
)
quota_reservations_expired_total = Counter(
    "quota_reservations_expired_total",
    "§23.2a — reservations reclaimed by the sweeper, never reconciled by the request path",
    ["pool"],
)

WINDOW_SECONDS = {"sync": 3600, "async": 86400}

# KEYS[1] = quota:{tenant_id}:{pool}   KEYS[2] = quota:reservation:{tenant_id}:{pool}:{run_id}
# ARGV[1] = estimated tokens   ARGV[2] = limit   ARGV[3] = window ttl seconds
# ARGV[4] = reservation ttl seconds   ARGV[5] = reservation JSON (estimated+deadline+quota_key)
_RESERVE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local estimated = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
if current + estimated > limit then
  local ttl = redis.call('TTL', KEYS[1])
  if ttl < 0 then ttl = tonumber(ARGV[3]) end
  return {0, current, limit, ttl}
end
redis.call('INCRBY', KEYS[1], estimated)
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[3])
  ttl = tonumber(ARGV[3])
end
redis.call('SET', KEYS[2], ARGV[5], 'EX', ARGV[4])
return {1, current + estimated, limit, ttl}
"""

# KEYS[1] = quota:reservation:{...}   ARGV[1] = actual tokens
# Returns the applied delta, or -1 if the reservation was already gone
# (reconciled twice, or swept out from under a very late caller).
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

# KEYS[1] = quota:reservation:{...}   ARGV[1] = now (unix seconds)
# Returns 1 if this call swept it (and thus must count the metric), 0 if
# already gone or not yet past its deadline.
_SWEEP_ONE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then
  return 0
end
local data = cjson.decode(raw)
if tonumber(ARGV[1]) <= tonumber(data['deadline']) then
  return 0
end
local removed = redis.call('DEL', KEYS[1])
if removed == 1 then
  redis.call('DECRBY', data['quota_key'], data['estimated'])
  return 1
end
return 0
"""


@dataclass
class ReservationResult:
    accepted: bool
    reservation_key: str | None  # None when rejected — nothing to reconcile
    tokens_after: int
    limit: int
    window_ttl_seconds: int

    @property
    def retry_after_seconds(self) -> int:
        return max(1, self.window_ttl_seconds)


def estimate_tokens(input_text: str, max_output_tokens: int) -> int:
    """No tokenizer dependency for a POC estimate — ~4 chars/token is the
    standard rough heuristic for English text (§6's own "estimated_tokens
    = input_tokens + max_output_tokens" doesn't specify how input_tokens
    is derived pre-call, since the real count is only known after the
    provider tokenizes it)."""
    estimated_input = max(1, len(input_text) // 4)
    return estimated_input + max_output_tokens


class QuotaManager:
    def __init__(self, redis: Redis, sync_limit: int, async_limit: int) -> None:
        self._redis = redis
        self._limits = {"sync": sync_limit, "async": async_limit}
        self._reserve = redis.register_script(_RESERVE_SCRIPT)
        self._reconcile_script = redis.register_script(_RECONCILE_SCRIPT)
        self._sweep_one = redis.register_script(_SWEEP_ONE_SCRIPT)

    async def reserve(
        self,
        *,
        tenant_id: str,
        pool: str,
        run_id: str,
        estimated_tokens: int,
        deadline_seconds: float,
    ) -> ReservationResult:
        quota_key = f"quota:{tenant_id}:{pool}"
        reservation_key = f"quota:reservation:{tenant_id}:{pool}:{run_id}"
        window_ttl = WINDOW_SECONDS[pool]
        reservation_ttl = int(deadline_seconds) + 60  # §23.2a grace period
        deadline = time.time() + deadline_seconds
        reservation_payload = json.dumps(
            {"estimated": estimated_tokens, "deadline": deadline, "quota_key": quota_key}
        )

        raw: list[Any] = await self._reserve(
            keys=[quota_key, reservation_key],
            args=[
                estimated_tokens,
                self._limits[pool],
                window_ttl,
                reservation_ttl,
                reservation_payload,
            ],
        )
        accepted, tokens_after, limit, ttl = raw
        if not accepted:
            quota_rejections_total.labels(tenant_id=tenant_id, pool=pool).inc()
        return ReservationResult(
            accepted=bool(accepted),
            reservation_key=reservation_key if accepted else None,
            tokens_after=int(tokens_after),
            limit=int(limit),
            window_ttl_seconds=int(ttl),
        )

    async def reconcile(self, reservation_key: str, actual_tokens: int) -> None:
        """Idempotent: a reservation already reclaimed by the sweeper (or
        reconciled twice) is silently a no-op, not an error — see the
        atomicity note in the module docstring."""
        await self._reconcile_script(keys=[reservation_key], args=[actual_tokens])

    async def sweep_once(self) -> int:
        """One pass over outstanding reservations, reclaiming any past
        their deadline. Returns the count swept this pass."""
        swept = 0
        now = time.time()
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match="quota:reservation:*", count=200)
            for key in keys:
                result = await self._sweep_one(keys=[key], args=[now])
                if result:
                    pool = (
                        key.decode().split(":")[3] if isinstance(key, bytes) else key.split(":")[3]
                    )
                    quota_reservations_expired_total.labels(pool=pool).inc()
                    swept += 1
            if cursor == 0:
                break
        return swept
