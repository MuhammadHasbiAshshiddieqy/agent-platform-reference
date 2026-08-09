"""§5.10/§6 L3 — the worker's own LiteLLM virtual key (`async-pool`),
budget-separated from the sync path's key: "endpoint async dengan limit
terpisah" is the requirement, and L3's whole point (§6's table) is a
circuit breaker on USD spend that's independent per caller.

LiteLLM's `/key/generate` only ever returns the raw key value at
creation time — there's no documented "fetch the existing secret back by
alias" endpoint to rely on. Rather than guess at that API surface, the
generated key is persisted to a local file (bind-mounted, §17) on first
successful generation; every later worker startup reads it back and
skips calling LiteLLM again entirely. Simpler and more robust for a POC
than trying to make `/key/generate` itself idempotent server-side.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

ASYNC_POOL_MODELS = ["agent-primary", "agent-cheap", "agent-local", "embedding-default"]


async def provision_async_pool_key(
    *,
    model_router_url: str,
    master_key: str,
    key_alias: str,
    daily_budget_usd: float,
    persist_path: Path,
    timeout_seconds: float = 30.0,
) -> str:
    if persist_path.exists():
        data = json.loads(persist_path.read_text())
        return str(data["key"])

    async with httpx.AsyncClient(base_url=model_router_url, timeout=timeout_seconds) as client:
        response = await client.post(
            "/key/generate",
            headers={"Authorization": f"Bearer {master_key}"},
            json={
                "models": ASYNC_POOL_MODELS,
                "max_budget": daily_budget_usd,
                "budget_duration": "24h",
                "key_alias": key_alias,
            },
        )
        response.raise_for_status()
        key = str(response.json()["key"])

    persist_path.parent.mkdir(parents=True, exist_ok=True)
    persist_path.write_text(json.dumps({"key": key, "key_alias": key_alias}))
    return key
