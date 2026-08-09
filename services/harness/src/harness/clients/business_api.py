"""§5.3 — harness calls business-api for mutations only through this
client; it's the only place in harness allowed to know the `/hr/v1/`,
`/payroll/v1/` URL shape (§5.12/§8.4). §22.5's RFC 8693 token exchange
(downscoped, 60s tokens) applies to `preview` calls whose tool manifest
declares `required_scopes_for_token_exchange` — `access_token` is
optional so the two readonly/no-scope tools keep using the same
internal-hop trust model (`X-Actor-*` headers, §7.1) every other
service-to-service call in this stack uses, unchanged from M5.
"""

from __future__ import annotations

from typing import Any

import httpx
from contracts.business_api import ExecuteResponse, PreviewResponse


class BusinessApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"business-api {status_code}: {detail}")


class BusinessApiClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds)

    def _headers(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        trace_id: str,
        access_token: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "X-Trace-Id": trace_id,
            "X-Tenant-Id": tenant_id,
            "X-Actor-Id": actor_id,
            "X-Actor-Type": "agent",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    async def query(
        self,
        *,
        domain: str,
        action: str,
        params: dict[str, Any],
        tenant_id: str,
        actor_id: str,
        trace_id: str,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"/{domain}/v1/actions/{action}/query",
            json={"params": params},
            headers=self._headers(
                tenant_id=tenant_id, actor_id=actor_id, trace_id=trace_id, access_token=access_token
            ),
        )
        if response.status_code >= 400:
            raise BusinessApiError(response.status_code, response.text)
        result: dict[str, Any] = response.json()
        return result

    async def preview(
        self,
        *,
        domain: str,
        action: str,
        params: dict[str, Any],
        tenant_id: str,
        actor_id: str,
        trace_id: str,
        access_token: str | None = None,
    ) -> PreviewResponse:
        response = await self._client.post(
            f"/{domain}/v1/actions/{action}/preview",
            json={"params": params},
            headers=self._headers(
                tenant_id=tenant_id, actor_id=actor_id, trace_id=trace_id, access_token=access_token
            ),
        )
        if response.status_code >= 400:
            raise BusinessApiError(response.status_code, response.text)
        return PreviewResponse.model_validate(response.json())

    async def execute(
        self,
        *,
        domain: str,
        action: str,
        preview_token: str,
        approval_id: str | None,
        idempotency_key: str,
        tenant_id: str,
        actor_id: str,
        trace_id: str,
    ) -> ExecuteResponse:
        headers = self._headers(tenant_id=tenant_id, actor_id=actor_id, trace_id=trace_id)
        headers["Idempotency-Key"] = idempotency_key
        response = await self._client.post(
            f"/{domain}/v1/actions/{action}/execute",
            json={"preview_token": preview_token, "approval_id": approval_id},
            headers=headers,
        )
        if response.status_code >= 400:
            raise BusinessApiError(response.status_code, response.text)
        return ExecuteResponse.model_validate(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()
