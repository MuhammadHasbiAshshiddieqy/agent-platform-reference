"""Reranking only — Infinity called directly, not through model-router
(see config.Settings' comment on why). §5.5 failure mode: if this call
fails, the caller falls back to the fused (dense+sparse) order unreranked
and marks the response `degraded: ["rerank"]` — never a hard failure just
because the cross-encoder is down.
"""

from __future__ import annotations

import httpx


class RerankClient:
    def __init__(self, base_url: str, model: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)
        self._model = model

    async def rerank(self, query: str, documents: list[str]) -> list[int]:
        """Returns document indices, best match first."""
        response = await self._client.post(
            "/rerank",
            json={"model": self._model, "query": query, "documents": documents},
        )
        response.raise_for_status()
        results = response.json()["results"]
        ordered = sorted(results, key=lambda r: r["relevance_score"], reverse=True)
        return [int(r["index"]) for r in ordered]

    async def aclose(self) -> None:
        await self._client.aclose()
