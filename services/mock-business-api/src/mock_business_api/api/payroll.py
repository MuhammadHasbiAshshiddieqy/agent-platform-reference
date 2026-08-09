"""§24.2 domain seed, payroll adapter: `adjust_payroll` — always
`risk_level: high` (§8.4: "aksi bersifat finansial... selalu risk_level:
high, tanpa pengecualian"), no exception logic needed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from contracts.business_api import (
    ExecuteRequest,
    ExecuteResponse,
    MutationEffect,
    PreviewRequest,
    PreviewResponse,
)
from contracts.common import RiskLevel
from contracts.tools import AdjustPayrollParams
from fastapi import APIRouter, HTTPException, Request, status

from mock_business_api.auth import authorize, extract_actor_headers, verify_token_exchange
from mock_business_api.failure_injection import maybe_simulate_failure
from mock_business_api.idempotency import IdempotencyStore
from mock_business_api.preview_tokens import PreviewTokenStore
from mock_business_api.state import BusinessState

router = APIRouter(prefix="/payroll/v1")


def _state(request: Request) -> BusinessState:
    return request.app.state.business_state  # type: ignore[no-any-return]


def _preview_tokens(request: Request) -> PreviewTokenStore:
    return request.app.state.preview_tokens  # type: ignore[no-any-return]


def _idempotency(request: Request) -> IdempotencyStore:
    return request.app.state.idempotency  # type: ignore[no-any-return]


@router.post("/actions/adjust_payroll/preview", response_model=PreviewResponse)
async def preview_adjust_payroll(body: PreviewRequest, request: Request) -> PreviewResponse:
    headers = extract_actor_headers(request, require_idempotency=False)
    await maybe_simulate_failure(request, phase="preview")

    state = _state(request)
    authorize(state, headers, action="adjust_payroll")
    verify_token_exchange(request, headers, action="adjust_payroll")
    params = AdjustPayrollParams.model_validate(body.params)
    # data_scope: tenant (§24.2) — any employee_id in this tenant is a
    # valid target, no self-scope check (unlike hr.py's two actions).

    token = await _preview_tokens(request).issue(
        action="adjust_payroll",
        params=params.model_dump(mode="json"),
        tenant_id=headers.tenant_id,
        requires_approval=True,
    )

    return PreviewResponse(
        action="adjust_payroll",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        effects=[
            MutationEffect(
                resource="payroll",
                id=params.employee_id,
                operation="adjust",
                to=f"{params.adjustment_percent:+.1f}%",
            )
        ],
        reversible=False,
        preview_token=token,
        validation_errors=[],
    )


@router.post("/actions/adjust_payroll/execute", response_model=ExecuteResponse)
async def execute_adjust_payroll(body: ExecuteRequest, request: Request) -> ExecuteResponse:
    headers = extract_actor_headers(request, require_idempotency=True)
    await maybe_simulate_failure(request, phase="execute")

    state = _state(request)
    authorize(state, headers, action="adjust_payroll")

    assert headers.idempotency_key is not None
    idempotency = _idempotency(request)
    existing = await idempotency.get(headers.tenant_id, headers.idempotency_key)
    if existing is not None:
        return existing

    entry = await _preview_tokens(request).consume(
        body.preview_token, tenant_id=headers.tenant_id, action="adjust_payroll"
    )
    if entry.requires_approval and not body.approval_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="approval_id required — this action needs approval"
        )

    adjustment_id = f"pra_{uuid.uuid4().hex[:20]}"
    response = ExecuteResponse(
        action="adjust_payroll",
        status="executed",
        business_ref=adjustment_id,
        executed_at=datetime.now(UTC),
    )
    await idempotency.put(headers.tenant_id, headers.idempotency_key, response)
    return response
