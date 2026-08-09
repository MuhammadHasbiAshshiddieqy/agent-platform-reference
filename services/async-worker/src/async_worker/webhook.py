"""§5.9/§5.10's webhook delivery — HMAC-SHA256 over the exact JSON bytes
sent, header `X-Duta-Signature: sha256=<hex>` (the same
`hmac.compare_digest`-style verification a receiver would run is proven
live in `tests/integration/test_m6_async.py`'s test receiver). Retried 5x
with exponential backoff (§5.9's DoD: "webhook terverifikasi
signature-nya oleh receiver" doesn't pin an exact schedule — this file's
choice, 1s/2s/4s/8s/16s, is a reasonable default, injectable in tests so
the live suite doesn't spend 30+ seconds waiting on a demo webhook).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac

import httpx
from contracts.jobs import WebhookPayload

DEFAULT_BACKOFF_SECONDS = [1.0, 2.0, 4.0, 8.0, 16.0]


def sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WebhookSender:
    def __init__(
        self,
        *,
        secrets: dict[str, str],
        timeout_seconds: float,
        backoff_seconds: list[float] | None = None,
    ) -> None:
        self._secrets = secrets
        self._client = httpx.AsyncClient(timeout=timeout_seconds)
        self._backoff = backoff_seconds if backoff_seconds is not None else DEFAULT_BACKOFF_SECONDS

    async def send(self, *, url: str, secret_ref: str | None, payload: WebhookPayload) -> bool:
        """Returns True once delivered (2xx), False after every attempt
        in the backoff schedule is exhausted — the caller (`processor.py`)
        records `webhook_failed` on `jobs.async_jobs.callback_status` in
        that case, per §5.10's failure mode: the tenant can still poll."""
        body = payload.model_dump_json().encode()
        headers = {"Content-Type": "application/json"}
        if secret_ref is not None:
            secret = self._secrets.get(secret_ref)
            if secret is not None:
                headers["X-Duta-Signature"] = sign(body, secret)

        attempts = len(self._backoff) + 1
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.post(url, content=body, headers=headers)
                if response.status_code < 300:
                    return True
            except httpx.HTTPError:
                pass
            if attempt < attempts:
                await asyncio.sleep(self._backoff[attempt - 1])
        return False

    async def aclose(self) -> None:
        await self._client.aclose()
