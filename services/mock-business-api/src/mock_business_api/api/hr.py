"""§24.2 domain seed, HR adapter: `get_leave_balance` (readonly) and
`submit_leave_request` (mutation, §8.4 preview/execute). Mounted at
`/hr/v1/...` — one container serving three URL prefixes as three adapters
(§5.12's compaction for the POC; routing and the contract itself are
real).
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
from contracts.tools import GetLeaveBalanceParams, SubmitLeaveRequestParams
from fastapi import APIRouter, HTTPException, Request, status

from mock_business_api.auth import (
    authorize,
    enforce_self_scope,
    extract_actor_headers,
    verify_token_exchange,
)
from mock_business_api.failure_injection import maybe_simulate_failure
from mock_business_api.idempotency import IdempotencyStore
from mock_business_api.preview_tokens import PreviewTokenStore
from mock_business_api.state import BusinessState

router = APIRouter(prefix="/hr/v1")

# leave_days > 5 escalates medium -> high (§26.2 demo step 4). Written
# here, in business logic, not just in a harness-side manifest — the
# spec's own §26.1 rationale: the *same* rule lives in
# kebijakan-cuti-2026.md's text, so the agent can cite policy for why an
# 8-day request needed approval instead of just obeying an opaque config.
LEAVE_DAYS_HIGH_RISK_THRESHOLD = 5


def _state(request: Request) -> BusinessState:
    return request.app.state.business_state  # type: ignore[no-any-return]


def _preview_tokens(request: Request) -> PreviewTokenStore:
    return request.app.state.preview_tokens  # type: ignore[no-any-return]


def _idempotency(request: Request) -> IdempotencyStore:
    return request.app.state.idempotency  # type: ignore[no-any-return]


@router.post("/actions/get_leave_balance/query")
async def query_get_leave_balance(body: PreviewRequest, request: Request) -> dict[str, object]:
    headers = extract_actor_headers(request, require_idempotency=False)
    await maybe_simulate_failure(request, phase="query")

    state = _state(request)
    user = authorize(state, headers, action="get_leave_balance")
    params = GetLeaveBalanceParams.model_validate(body.params)
    enforce_self_scope(user, action="get_leave_balance", employee_id=params.employee_id)

    balance = state.leave_balances.get(params.employee_id)
    if balance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown employee_id")
    return {"employee_id": params.employee_id, "leave_balance": balance}


@router.post("/actions/submit_leave_request/preview", response_model=PreviewResponse)
async def preview_submit_leave_request(body: PreviewRequest, request: Request) -> PreviewResponse:
    headers = extract_actor_headers(request, require_idempotency=False)
    await maybe_simulate_failure(request, phase="preview")

    state = _state(request)
    user = authorize(state, headers, action="submit_leave_request")
    verify_token_exchange(request, headers, action="submit_leave_request")
    params = SubmitLeaveRequestParams.model_validate(body.params)
    enforce_self_scope(user, action="submit_leave_request", employee_id=params.employee_id)

    balance = state.leave_balances.get(params.employee_id, 0)
    validation_errors: list[str] = []
    if params.leave_days > balance:
        validation_errors.append(
            f"insufficient leave balance: {balance} available, {params.leave_days} requested"
        )

    risk_level = (
        RiskLevel.HIGH if params.leave_days > LEAVE_DAYS_HIGH_RISK_THRESHOLD else RiskLevel.MEDIUM
    )
    requires_approval = risk_level == RiskLevel.HIGH

    token = await _preview_tokens(request).issue(
        action="submit_leave_request",
        params=params.model_dump(mode="json"),
        tenant_id=headers.tenant_id,
        requires_approval=requires_approval,
    )

    return PreviewResponse(
        action="submit_leave_request",
        risk_level=risk_level,
        requires_approval=requires_approval,
        effects=[
            # `from_=` (not the `from` alias) — StrictModel sets
            # `populate_by_name=True`, so the Python attribute name works
            # at runtime; mypy's pydantic stub only knows the alias, hence
            # the ignore.
            MutationEffect(
                resource="leave_balance",
                id=params.employee_id,
                from_=balance,  # type: ignore[call-arg]
                to=balance - params.leave_days,
            ),
            MutationEffect(resource="leave_request", id=None, operation="create"),
        ],
        reversible=True,
        preview_token=token,
        validation_errors=validation_errors,
    )


@router.post("/actions/submit_leave_request/execute", response_model=ExecuteResponse)
async def execute_submit_leave_request(body: ExecuteRequest, request: Request) -> ExecuteResponse:
    headers = extract_actor_headers(request, require_idempotency=True)
    await maybe_simulate_failure(request, phase="execute")

    state = _state(request)
    authorize(state, headers, action="submit_leave_request")

    assert headers.idempotency_key is not None  # required=True above guarantees this
    idempotency = _idempotency(request)
    existing = await idempotency.get(headers.tenant_id, headers.idempotency_key)
    if existing is not None:
        return existing

    entry = await _preview_tokens(request).consume(
        body.preview_token, tenant_id=headers.tenant_id, action="submit_leave_request"
    )
    if entry.requires_approval and not body.approval_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="approval_id required — this action needs approval"
        )

    params = SubmitLeaveRequestParams.model_validate(entry.params)
    await state.deduct_leave(params.employee_id, params.leave_days)
    request_id = f"lvr_{uuid.uuid4().hex[:20]}"
    await state.record_leave_request(
        {
            "request_id": request_id,
            "employee_id": params.employee_id,
            "leave_days": params.leave_days,
            "start_date": params.start_date.isoformat(),
            "approval_id": body.approval_id,
        }
    )

    response = ExecuteResponse(
        action="submit_leave_request",
        status="executed",
        business_ref=request_id,
        executed_at=datetime.now(UTC),
    )
    await idempotency.put(headers.tenant_id, headers.idempotency_key, response)
    return response
