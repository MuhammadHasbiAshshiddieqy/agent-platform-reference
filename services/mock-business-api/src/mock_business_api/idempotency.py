"""Business-api's own idempotency layer for `execute` — distinct from
`agent-gateway`'s (`Idempotency-Key` on `/v1/agent/invoke` protects
against duplicate *agent runs*; this one, on `execute`, protects against
duplicate *mutations* even if harness retries the same execute call
after e.g. a network timeout on its side). §23.2i names this as the
second of two layers, the first being the `UPDATE ... WHERE status =
'awaiting_approval'` race-safe approval transition in harness.
"""

from __future__ import annotations

import asyncio

from contracts.business_api import ExecuteResponse


class IdempotencyStore:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], ExecuteResponse] = {}
        self._lock = asyncio.Lock()

    async def get(self, tenant_id: str, key: str) -> ExecuteResponse | None:
        async with self._lock:
            return self._store.get((tenant_id, key))

    async def put(self, tenant_id: str, key: str, response: ExecuteResponse) -> None:
        async with self._lock:
            self._store[(tenant_id, key)] = response
