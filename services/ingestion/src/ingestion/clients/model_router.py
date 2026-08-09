"""§5.4 — embedding generation goes through model-router (`embedding-default`
alias -> Infinity, config/model-router/config.yaml), same rule as every
other model call in this repo: no provider/server name in application code.
"""

from __future__ import annotations

import httpx


class EmbeddingClient:
    def __init__(self, base_url: str, api_key: str, model_alias: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )
        self._model_alias = model_alias

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.post(
            "/embeddings", json={"model": self._model_alias, "input": texts}
        )
        response.raise_for_status()
        body = response.json()
        return [item["embedding"] for item in body["data"]]

    async def aclose(self) -> None:
        await self._client.aclose()
