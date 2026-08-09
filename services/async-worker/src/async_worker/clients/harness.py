"""§5.10 — worker is an *executor*, not an *orchestrator*: it calls
harness's exact same internal endpoint the sync path uses
(`POST /internal/v1/runs`), with `execution_mode=async` already set on
the `AgentRunRequest` by gateway before the message was ever published.
No agent-loop logic lives here.
"""

from __future__ import annotations

import httpx
from contracts.gateway import AgentInvokeResponse
from contracts.harness import AgentRunRequest


class HarnessError(Exception):
    def __init__(self, status_code: int, detail: str, *, retriable: bool) -> None:
        self.status_code = status_code
        self.detail = detail
        self.retriable = retriable
        super().__init__(f"harness {status_code}: {detail}")


class HarnessClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    async def run(self, request: AgentRunRequest) -> AgentInvokeResponse:
        try:
            response = await self._client.post(
                "/internal/v1/runs",
                content=request.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise HarnessError(504, str(exc), retriable=True) from exc
        except httpx.HTTPError as exc:
            raise HarnessError(503, str(exc), retriable=True) from exc

        if response.status_code >= 500:
            raise HarnessError(response.status_code, response.text, retriable=True)
        if response.status_code >= 400:
            # §5.9's DoD: only retriable errors (5xx, timeout) get the
            # backoff-and-retry treatment — a 4xx (bad input, refused by
            # a guardrail) will fail identically on every retry.
            raise HarnessError(response.status_code, response.text, retriable=False)

        return AgentInvokeResponse.model_validate(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()
