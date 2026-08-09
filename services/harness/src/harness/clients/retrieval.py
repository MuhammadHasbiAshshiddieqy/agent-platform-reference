"""§5.3 — harness calls retrieval-service for RAG; it never queries
Postgres or pgvector itself (that's retrieval-service's job, §5.5)."""

from __future__ import annotations

import httpx
from contracts.retrieval import SearchRequest, SearchResult


class RetrievalClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    async def search(self, request: SearchRequest) -> SearchResult:
        response = await self._client.post(
            "/internal/v1/search",
            content=request.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return SearchResult.model_validate(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()
