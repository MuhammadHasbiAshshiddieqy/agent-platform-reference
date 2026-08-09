"""§8.2 — the gateway is the only caller of `POST /internal/v1/runs` in
the sync path (async-worker calls it too, from M6, over the same
contract). Gateway never calls model-router or retrieval directly — the
harness owns all of that (§5.2 "bukan tanggung jawab": memanggil LLM)."""

from __future__ import annotations

import httpx
from contracts.gateway import AgentInvokeResponse
from contracts.harness import (
    AgentRunRequest,
    ApprovalDecisionInternalRequest,
    KillswitchInternalRequest,
)


class HarnessClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    async def run(self, request: AgentRunRequest) -> AgentInvokeResponse:
        # `content=` (not `json=`) to control serialization exactly via
        # our own model_dump_json() — but httpx does NOT set Content-Type
        # for `content=`, and FastAPI silently fails to parse the body as
        # JSON without it (422, not a 4xx that explains why). Must be set
        # explicitly.
        response = await self._client.post(
            "/internal/v1/runs",
            content=request.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return AgentInvokeResponse.model_validate(response.json())

    async def decide_approval(
        self, approval_id: str, request: ApprovalDecisionInternalRequest
    ) -> httpx.Response:
        # Returned raw (not parsed/raise_for_status'd) — the caller needs
        # to distinguish 403/404/409 from harness and translate each to
        # the right public status code, not just "harness is down".
        return await self._client.post(
            f"/internal/v1/approvals/{approval_id}/decision",
            content=request.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )

    async def set_tool_killswitch(
        self, tool_name: str, request: KillswitchInternalRequest
    ) -> httpx.Response:
        return await self._client.post(
            f"/internal/v1/admin/killswitch/tools/{tool_name}",
            content=request.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )

    async def set_agent_killswitch(
        self, agent_id: str, request: KillswitchInternalRequest
    ) -> httpx.Response:
        return await self._client.post(
            f"/internal/v1/admin/killswitch/agents/{agent_id}",
            content=request.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
