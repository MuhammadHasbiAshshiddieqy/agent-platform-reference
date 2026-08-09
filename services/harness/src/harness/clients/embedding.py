"""§10 step 2 — harness embeds the (normalized) query directly via
model-router for semantic-cache lookup, independent of retrieval-
service's own query embedding for RAG. Same pattern as `services/
retrieval/src/retrieval/clients/model_router.py`'s `EmbeddingClient`, not
imported from there (boundary #1) — a cache lookup must keep working even
when retrieval-service itself is down, so the two embedding call sites
are deliberately not coupled to one client instance.
"""

from __future__ import annotations

import httpx


class EmbeddingClient:
    def __init__(
        self, base_url: str, api_key: str, model_alias: str, timeout_seconds: float = 30.0
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
        )
        self._model_alias = model_alias

    async def embed_query(self, text: str) -> list[float]:
        response = await self._client.post(
            "/embeddings", json={"model": self._model_alias, "input": [text]}
        )
        response.raise_for_status()
        return list(response.json()["data"][0]["embedding"])

    async def aclose(self) -> None:
        await self._client.aclose()
