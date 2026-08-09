"""§13.7 — one token per item, minted via mock-idp's `/oauth/eval-
impersonate` (never the general `/oauth/token` dev-login — see that
endpoint's own rejection behavior in `services/mock-idp/src/mock_idp/
main.py` for why this is a distinct, tenant-gated path).
"""

from __future__ import annotations

import httpx


class MockIdpClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    async def impersonate(self, user_id: str) -> str:
        response = await self._client.post("/oauth/eval-impersonate", json={"user_id": user_id})
        response.raise_for_status()
        token: str = response.json()["access_token"]
        return token

    async def aclose(self) -> None:
        await self._client.aclose()
