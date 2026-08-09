"""§5.4 — query embedding goes through model-router (`embedding-default`
alias), same rule as everywhere else: no provider/server name in
application code."""

from __future__ import annotations

import httpx


class EmbeddingClient:
    def __init__(self, base_url: str, api_key: str, model_alias: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
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
