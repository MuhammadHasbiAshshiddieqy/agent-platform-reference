"""§13.7 — eval-service calls the PUBLIC gateway, the same
`/v1/agent/invoke` a real user hits, with `X-Eval-Mode: true` set so
harness attaches the `_eval` debug bundle (§13.1) — never harness
directly, so quota/idempotency/guardrails are all genuinely exercised,
not bypassed for convenience.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
from contracts.common import Headers
from contracts.gateway import AgentInvokeResponse


class GatewayError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class GatewayClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 180.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    async def invoke(self, *, token: str, agent_id: str, question: str) -> AgentInvokeResponse:
        headers = {
            Headers.AUTHORIZATION: f"Bearer {token}",
            Headers.IDEMPOTENCY_KEY: str(uuid.uuid4()),
            Headers.EVAL_MODE: "true",
        }
        body = {"agent_id": agent_id, "input": {"type": "text", "content": question}}

        attempt = 0
        while True:
            attempt += 1
            response = await self._client.post("/v1/agent/invoke", headers=headers, json=body)
            if response.status_code == 200:
                return AgentInvokeResponse.model_validate(response.json())
            # §13.7 — retry on 429 (quota)/503 (transient upstream), same
            # transient-vs-terminal split async-worker's own HarnessError
            # already draws for its own retry ladder.
            if response.status_code in (429, 503) and attempt <= self._max_retries:
                await asyncio.sleep(self._retry_backoff_seconds * attempt)
                continue
            raise GatewayError(response.status_code, response.text)

    async def aclose(self) -> None:
        await self._client.aclose()
