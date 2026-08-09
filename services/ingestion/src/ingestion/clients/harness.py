"""§10's document-changed event — a direct call to harness's semantic
cache invalidation endpoint, not a message bus: this is a low-volume,
best-effort side effect of ingestion (one call per run, not per
request), so the added moving parts of a broker would buy nothing a
plain HTTP call + TTL backstop doesn't already cover. Best-effort by
design: a failure here logs and lets the ingestion run finish
successfully — a stale cache entry is bounded by its own TTL (§10, 1h/24h
default), whereas failing the whole ingestion run over a downstream
cache-invalidation hiccup would be a worse outcome.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("ingestion.clients.harness")


class HarnessCacheClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    async def invalidate(self, *, tenant_id: str, document_ids: list[str]) -> None:
        if not document_ids:
            return
        try:
            response = await self._client.post(
                "/internal/v1/cache/invalidate",
                json={"tenant_id": tenant_id, "document_ids": document_ids},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning(
                "cache invalidation call to harness failed for tenant=%s docs=%d "
                "(non-fatal — TTL is the backstop)",
                tenant_id,
                len(document_ids),
                exc_info=True,
            )

    async def aclose(self) -> None:
        await self._client.aclose()
