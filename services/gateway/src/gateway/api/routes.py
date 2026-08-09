"""§8.1 — the only public agent API. Schema validation is FastAPI/Pydantic
parsing `AgentInvokeRequest`; idempotency (§23.2b), quota reservation
(§6 L2 / §23.2a), and the sync proxy to harness are what this module has
to get right. Job routing (async, M6) doesn't exist yet.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import UTC, datetime

import httpx
from contracts.agent import TokenBudget
from contracts.authz import KillswitchRequest, KillswitchResponse
from contracts.common import ExecutionMode, Headers, JobStatus
from contracts.gateway import (
    AgentInvokeRequest,
    AgentInvokeResponse,
    AgentJobAcceptedResponse,
    AgentJobRequest,
    AgentJobStatusResponse,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    JobError,
)
from contracts.harness import (
    AgentRunRequest,
    ApprovalDecisionInternalRequest,
    KillswitchInternalRequest,
)
from contracts.jobs import AsyncJobMessage
from fastapi import APIRouter, HTTPException, Request, Response, status
from gateway.auth import authenticate
from gateway.clients.harness import HarnessClient
from gateway.clients.rabbitmq import RabbitMQPublisher
from gateway.persistence.db import tenant_session
from gateway.persistence.idempotency import complete, get_existing, try_begin
from gateway.persistence.jobs import get_async_job, insert_async_job
from gateway.quota import QuotaManager, estimate_tokens

router = APIRouter()

KILLSWITCH_MANAGE_PERMISSION = "platform.killswitch.manage"  # §22.6


@router.post("/v1/agent/invoke", response_model=AgentInvokeResponse)
async def invoke(
    body: AgentInvokeRequest, request: Request, response: Response
) -> AgentInvokeResponse:
    auth = authenticate(request)

    idempotency_key = request.headers.get(Headers.IDEMPOTENCY_KEY)
    if not idempotency_key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"{Headers.IDEMPOTENCY_KEY} header is required"
        )

    trace_id = request.headers.get(Headers.TRACE_ID) or f"trc_{uuid.uuid4().hex[:20]}"
    response.headers[Headers.TRACE_ID] = trace_id

    request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()

    # §23.2b — INSERT first decides the race; a SELECT-then-INSERT here
    # would let two concurrent requests with the same key both proceed.
    async with tenant_session(auth.tenant_id) as conn:
        won = await try_begin(
            conn,
            tenant_id=auth.tenant_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if not won:
            existing = await get_existing(
                conn, tenant_id=auth.tenant_id, idempotency_key=idempotency_key
            )
            if existing is None:  # pragma: no cover — lost the race, then the row vanished
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key reused with a different request body",
                )
            if existing.response_body is not None:
                response.status_code = existing.status_code or status.HTTP_200_OK
                return AgentInvokeResponse.model_validate(existing.response_body)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="A request with this Idempotency-Key is still in progress",
                headers={"Retry-After": "2"},
            )

    run_id = f"run_{uuid.uuid4().hex[:20]}"

    # request.app.state is untyped (Starlette State is a plain namespace) —
    # annotate explicitly so mypy tracks real types below instead of
    # silently widening everything to Any.
    quota: QuotaManager = request.app.state.quota
    harness: HarnessClient = request.app.state.harness
    sync_timeout: float = request.app.state.sync_timeout_seconds

    estimated_tokens = estimate_tokens(body.input.content, body.options.max_output_tokens)
    reservation = await quota.reserve(
        tenant_id=auth.tenant_id,
        pool="sync",
        run_id=run_id,
        estimated_tokens=estimated_tokens,
        deadline_seconds=sync_timeout,
    )
    if not reservation.accepted:
        quota_reset_at = datetime.fromtimestamp(
            time.time() + reservation.retry_after_seconds, tz=UTC
        ).isoformat()
        error_body = {
            "error": "quota_exceeded",
            "trace_id": trace_id,
            "quota_reset_at": quota_reset_at,
        }
        async with tenant_session(auth.tenant_id) as conn:
            await complete(
                conn,
                tenant_id=auth.tenant_id,
                idempotency_key=idempotency_key,
                response_body=error_body,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_body,
            headers={"Retry-After": str(reservation.retry_after_seconds)},
        )

    # Accepted implies reservation_key is set (see ReservationResult) —
    # narrow the type once instead of asserting at each reconcile() call.
    assert reservation.reservation_key is not None
    reservation_key = reservation.reservation_key

    run_request = AgentRunRequest(
        run_id=run_id,
        trace_id=trace_id,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        acl_group_ids=auth.acl_group_ids,
        employee_id=auth.employee_id,
        permissions=auth.permissions,
        roles=auth.roles,
        scope_context=auth.scope_context,
        subject_token=auth.raw_token,
        agent_id=body.agent_id,
        conversation_id=body.conversation_id,
        input=body.input,
        options=body.options,
        execution_mode=ExecutionMode.SYNC,
        budget=TokenBudget(pool="sync", reserved_tokens=estimated_tokens),
        # §13.1 — forwarded as a raw signal only; harness alone decides
        # whether `auth.tenant_id` is actually eligible (EVAL_TENANT_IDS,
        # its own setting) and logs `eval_mode_abuse` if not. Gateway
        # doesn't duplicate that check here — §8.4's "no service trusts
        # its caller" cuts both ways: the party that can actually audit
        # a denial (harness, `audit` schema owner) should be the one
        # deciding it, not a second copy of the same tenant allowlist.
        eval_mode_requested=request.headers.get(Headers.EVAL_MODE, "").lower() == "true",
    )

    try:
        result = await harness.run(run_request)
    except httpx.HTTPError as exc:
        # Reconcile with actual=0: the run never produced usage, so the
        # full reservation returns to the tenant's bucket immediately
        # rather than waiting on the sweeper (§23.2a) — that's the
        # crash-recovery path, not the normal-failure path.
        await quota.reconcile(reservation_key, actual_tokens=0)
        status_code = (
            status.HTTP_504_GATEWAY_TIMEOUT
            if isinstance(exc, httpx.TimeoutException)
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        # Complete the idempotency row even on failure — otherwise a
        # retry with the same key hangs behind the 409 "in progress"
        # branch forever instead of getting a clean re-attempt.
        async with tenant_session(auth.tenant_id) as conn:
            await complete(
                conn,
                tenant_id=auth.tenant_id,
                idempotency_key=idempotency_key,
                response_body={"error": str(exc), "trace_id": trace_id},
                status_code=status_code,
            )
        raise HTTPException(status_code, detail="agent-harness unavailable") from exc

    actual_tokens = result.usage.input_tokens + result.usage.output_tokens
    await quota.reconcile(reservation_key, actual_tokens=actual_tokens)

    async with tenant_session(auth.tenant_id) as conn:
        await complete(
            conn,
            tenant_id=auth.tenant_id,
            idempotency_key=idempotency_key,
            response_body=result.model_dump(mode="json"),
            status_code=status.HTTP_200_OK,
        )

    return result


@router.post(
    "/v1/agent/jobs", response_model=AgentJobAcceptedResponse, status_code=status.HTTP_202_ACCEPTED
)
async def submit_job(
    body: AgentJobRequest, request: Request, response: Response
) -> AgentJobAcceptedResponse:
    """§8.1/§5.9 — the async twin of `/v1/agent/invoke`: reserves against
    the *async* quota pool (never sync's — §6's whole point is that a
    bulk job can't starve interactive chat), writes a `queued` row to
    `jobs.async_jobs`, and publishes to RabbitMQ. `async-worker` does
    everything from here on; gateway's job is done once the message is on
    the exchange."""
    auth = authenticate(request)

    idempotency_key = request.headers.get(Headers.IDEMPOTENCY_KEY)
    if not idempotency_key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"{Headers.IDEMPOTENCY_KEY} header is required"
        )

    trace_id = request.headers.get(Headers.TRACE_ID) or f"trc_{uuid.uuid4().hex[:20]}"
    response.headers[Headers.TRACE_ID] = trace_id

    request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()

    async with tenant_session(auth.tenant_id) as conn:
        won = await try_begin(
            conn,
            tenant_id=auth.tenant_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if not won:
            existing = await get_existing(
                conn, tenant_id=auth.tenant_id, idempotency_key=idempotency_key
            )
            if existing is None:  # pragma: no cover — lost the race, then the row vanished
                raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key reused with a different request body",
                )
            if existing.response_body is not None:
                response.status_code = existing.status_code or status.HTTP_202_ACCEPTED
                return AgentJobAcceptedResponse.model_validate(existing.response_body)
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="A request with this Idempotency-Key is still in progress",
                headers={"Retry-After": "2"},
            )

    job_id = f"job_{uuid.uuid4().hex[:20]}"

    quota: QuotaManager = request.app.state.quota
    publisher: RabbitMQPublisher = request.app.state.rabbitmq
    async_deadline: float = request.app.state.async_job_deadline_seconds

    estimated_tokens = estimate_tokens(body.input.content, body.options.max_output_tokens)
    reservation = await quota.reserve(
        tenant_id=auth.tenant_id,
        pool="async",
        run_id=job_id,
        estimated_tokens=estimated_tokens,
        deadline_seconds=async_deadline,
    )
    if not reservation.accepted:
        quota_reset_at = datetime.fromtimestamp(
            time.time() + reservation.retry_after_seconds, tz=UTC
        ).isoformat()
        error_body = {
            "error": "quota_exceeded",
            "trace_id": trace_id,
            "quota_reset_at": quota_reset_at,
        }
        async with tenant_session(auth.tenant_id) as conn:
            await complete(
                conn,
                tenant_id=auth.tenant_id,
                idempotency_key=idempotency_key,
                response_body=error_body,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_body,
            headers={"Retry-After": str(reservation.retry_after_seconds)},
        )

    run_request = AgentRunRequest(
        run_id=job_id,
        trace_id=trace_id,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        acl_group_ids=auth.acl_group_ids,
        employee_id=auth.employee_id,
        permissions=auth.permissions,
        roles=auth.roles,
        scope_context=auth.scope_context,
        subject_token=auth.raw_token,
        agent_id=body.agent_id,
        conversation_id=body.conversation_id,
        input=body.input,
        options=body.options,
        execution_mode=ExecutionMode.ASYNC,
        budget=TokenBudget(pool="async", reserved_tokens=estimated_tokens),
    )

    async with tenant_session(auth.tenant_id) as conn:
        await insert_async_job(
            conn,
            job_id=job_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            trace_id=trace_id,
            priority=body.priority.value,
            payload=body.model_dump(mode="json"),
            callback_url=body.callback_url,
        )

    await publisher.publish_job(
        AsyncJobMessage(
            job_id=job_id,
            tenant_id=auth.tenant_id,
            priority=body.priority,
            run_request=run_request,
            callback_url=body.callback_url,
            callback_secret_ref=body.callback_secret_ref,
            quota_reservation_key=reservation.reservation_key,
        )
    )

    accepted = AgentJobAcceptedResponse(
        job_id=job_id, trace_id=trace_id, poll_url=f"/v1/agent/jobs/{job_id}"
    )
    async with tenant_session(auth.tenant_id) as conn:
        await complete(
            conn,
            tenant_id=auth.tenant_id,
            idempotency_key=idempotency_key,
            response_body=accepted.model_dump(mode="json"),
            status_code=status.HTTP_202_ACCEPTED,
        )
    return accepted


@router.get("/v1/agent/jobs/{job_id}", response_model=AgentJobStatusResponse)
async def get_job(job_id: str, request: Request) -> AgentJobStatusResponse:
    auth = authenticate(request)
    async with tenant_session(auth.tenant_id) as conn:
        row = await get_async_job(conn, tenant_id=auth.tenant_id, job_id=job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown job_id for this tenant")

    return AgentJobStatusResponse(
        job_id=row["id"],
        status=JobStatus(row["status"]),
        attempts=row["attempts"],
        result=AgentInvokeResponse.model_validate(row["result"]) if row["result"] else None,
        error=JobError.model_validate(row["error"]) if row["error"] else None,
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


@router.post("/v1/approvals/{approval_id}/decision", response_model=ApprovalDecisionResponse)
async def approval_decision(
    approval_id: str, body: ApprovalDecisionRequest, request: Request
) -> ApprovalDecisionResponse:
    """§8.1 — gateway only authenticates and proxies; harness owns `audit`
    (§5.6) so it's the one that reads/writes `audit.mutation_requests` and
    decides authorization (`harness/approvals.py`)."""
    auth = authenticate(request)

    trace_id = request.headers.get(Headers.TRACE_ID) or f"trc_{uuid.uuid4().hex[:20]}"

    harness: HarnessClient = request.app.state.harness
    internal_request = ApprovalDecisionInternalRequest(
        trace_id=trace_id,
        tenant_id=auth.tenant_id,
        approver_user_id=auth.user_id,
        approver_permissions=auth.permissions,
        decision=body.decision,
    )
    try:
        response = await harness.decide_approval(approval_id, internal_request)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent-harness unavailable"
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(response.status_code, detail=response.text)

    return ApprovalDecisionResponse.model_validate(response.json())


@router.post("/v1/admin/killswitch/tools/{tool_name}", response_model=KillswitchResponse)
async def admin_killswitch_tool(
    tool_name: str, body: KillswitchRequest, request: Request
) -> KillswitchResponse:
    """§22.6 — protected by `platform.killswitch.manage`; gateway only
    authenticates + authorizes + proxies (boundary #2 — harness owns
    `authz`)."""
    auth = authenticate(request)
    if KILLSWITCH_MANAGE_PERMISSION not in auth.permissions:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"missing required permission: {KILLSWITCH_MANAGE_PERMISSION}",
        )
    harness: HarnessClient = request.app.state.harness
    internal_request = KillswitchInternalRequest(
        disabled=body.disabled, reason=body.reason, changed_by=auth.user_id
    )
    try:
        response = await harness.set_tool_killswitch(tool_name, internal_request)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent-harness unavailable"
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, detail=response.text)
    return KillswitchResponse.model_validate(response.json())


@router.post("/v1/admin/killswitch/agents/{agent_id}", response_model=KillswitchResponse)
async def admin_killswitch_agent(
    agent_id: str, body: KillswitchRequest, request: Request
) -> KillswitchResponse:
    auth = authenticate(request)
    if KILLSWITCH_MANAGE_PERMISSION not in auth.permissions:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"missing required permission: {KILLSWITCH_MANAGE_PERMISSION}",
        )
    harness: HarnessClient = request.app.state.harness
    internal_request = KillswitchInternalRequest(
        disabled=body.disabled, reason=body.reason, changed_by=auth.user_id
    )
    try:
        response = await harness.set_agent_killswitch(agent_id, internal_request)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent-harness unavailable"
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, detail=response.text)
    return KillswitchResponse.model_validate(response.json())
