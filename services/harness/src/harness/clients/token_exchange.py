"""§22.5's RFC 8693 token exchange, harness's side of the call. Used only
for the `preview` step of a mutation whose manifest declares
`required_scopes_for_token_exchange` — §22.5's own diagram shows the
downscoped token flowing into `preview`, not `execute`; the deferred
human-approval `execute` (`approvals.py`) happens well after the
original request's JWT would be gone (JWTs are never persisted, by
design, §22.5's "tidak pernah disimpan") and keeps using the existing
`X-Actor-*` header mechanism instead — documented scope cut, not an
oversight (see `tools/executor.py`'s docstring).
"""

from __future__ import annotations

import httpx


class TokenExchangeError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"token-exchange {status_code}: {detail}")


class TokenExchangeClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    async def exchange(self, *, subject_token: str, audience: str, scope: str) -> str:
        response = await self._client.post(
            "/oauth/token-exchange",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": subject_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "audience": audience,
                "scope": scope,
            },
        )
        if response.status_code >= 400:
            raise TokenExchangeError(response.status_code, response.text)
        return str(response.json()["access_token"])

    async def aclose(self) -> None:
        await self._client.aclose()
