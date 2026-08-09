"""§24.3 — turns the mock from a stub into a demo prop: circuit breakers,
DLQs, and graceful degradation are hard to show live without a way to
make a dependency misbehave on command.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import HTTPException, Request, status

Phase = Literal["preview", "execute", "query"]


async def maybe_simulate_failure(request: Request, *, phase: Phase) -> None:
    simulate = request.headers.get("X-Simulate")
    if not simulate:
        return

    if simulate == "timeout":
        # Sleep well past any client timeout used in this repo (max ~160s
        # in tests/integration) so the caller's own timeout fires first —
        # that's the scenario being simulated, a hung dependency, not an
        # eventual response.
        await asyncio.sleep(600)
    elif simulate == "error_500":
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="simulated internal error"
        )
    elif simulate == "rate_limit":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="simulated rate limit",
            headers={"Retry-After": "5"},
        )
    elif simulate == "partial_failure":
        # "preview berhasil, execute gagal" — only bite on the execute
        # call, so callers can observe a preview that looked fine.
        if phase == "execute":
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="simulated partial failure")
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"unknown X-Simulate value: {simulate!r}"
        )
