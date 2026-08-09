from __future__ import annotations

from contracts.authz import KillswitchResponse
from contracts.gateway import AgentInvokeResponse
from contracts.harness import (
    AgentRunRequest,
    ApprovalDecisionInternalRequest,
    ApprovalDecisionInternalResponse,
    CacheInvalidateRequest,
    CacheInvalidateResponse,
    KillswitchInternalRequest,
)
from fastapi import APIRouter, HTTPException, Request, status
from harness.approvals import ApprovalError, decide_approval
from harness.graph.runner import run_agent
from harness.guardrails.errors import GuardrailServiceError
from harness.persistence.db import admin_session, tenant_session
from harness.persistence.repository import upsert_tool_override

router = APIRouter()


@router.post("/internal/v1/runs", response_model=AgentInvokeResponse)
async def create_run(request: AgentRunRequest, http_request: Request) -> AgentInvokeResponse:
    app = http_request.app
    try:
        return await run_agent(app.state.graph, app.state.langfuse, request, app.state.audience)
    except GuardrailServiceError as exc:
        # §9.2's hard rule: guardrails must not fail open. A guardrail
        # *check itself* being unreachable/broken is not the same as the
        # guardrail deciding "allow" — it must reject the request, not
        # silently let it through.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post(
    "/internal/v1/approvals/{approval_id}/decision", response_model=ApprovalDecisionInternalResponse
)
async def approval_decision(
    approval_id: str, body: ApprovalDecisionInternalRequest, http_request: Request
) -> ApprovalDecisionInternalResponse:
    app = http_request.app
    try:
        async with tenant_session(body.tenant_id) as conn:
            result = await decide_approval(
                conn,
                app.state.business_api,
                trace_id=body.trace_id,
                tenant_id=body.tenant_id,
                approval_id=approval_id,
                approver_user_id=body.approver_user_id,
                approver_permissions=body.approver_permissions,
                decision=body.decision,
                tool_manifests=app.state.tool_manifests,
            )
    except ApprovalError as exc:
        raise HTTPException(exc.status_code, detail=exc.detail) from exc

    return ApprovalDecisionInternalResponse(
        approval_id=result.approval_id,
        status=result.status,  # type: ignore[arg-type]
        mutation_status=result.mutation_status,
        business_ref=result.business_ref,
    )


@router.post("/internal/v1/admin/killswitch/tools/{tool_name}", response_model=KillswitchResponse)
async def killswitch_tool(
    tool_name: str, body: KillswitchInternalRequest, http_request: Request
) -> KillswitchResponse:
    """§22.6 — flips `killswitch:tool:{tool_name}` in Redis (what
    `KillswitchChecker`'s 10s-cached reads actually see) and durably
    records the change in `authz.tool_overrides` (§22.8)."""
    app = http_request.app
    redis = app.state.redis
    key = f"killswitch:tool:{tool_name}"
    if body.disabled:
        await redis.set(key, "disabled")
    else:
        await redis.delete(key)

    async with admin_session() as conn:
        await upsert_tool_override(
            conn,
            tool_name=tool_name,
            disabled=body.disabled,
            reason=body.reason,
            changed_by=body.changed_by,
        )

    return KillswitchResponse(name=tool_name, kind="tool", disabled=body.disabled)


@router.post("/internal/v1/admin/killswitch/agents/{agent_id}", response_model=KillswitchResponse)
async def killswitch_agent(
    agent_id: str, body: KillswitchInternalRequest, http_request: Request
) -> KillswitchResponse:
    # No `authz.tool_overrides`-equivalent table for agents (§22.8's
    # literal schema only declares one) — Redis is the only record.
    app = http_request.app
    redis = app.state.redis
    key = f"killswitch:agent:{agent_id}"
    if body.disabled:
        await redis.set(key, "disabled")
    else:
        await redis.delete(key)

    return KillswitchResponse(name=agent_id, kind="agent", disabled=body.disabled)


@router.post("/internal/v1/cache/invalidate", response_model=CacheInvalidateResponse)
async def cache_invalidate(
    body: CacheInvalidateRequest, http_request: Request
) -> CacheInvalidateResponse:
    """§10's invalidation rule — ingestion-service calls this right after
    a document changes (or is tombstoned). Best-effort from the caller's
    side (`ingestion/clients/harness.py` logs and continues on failure
    rather than failing the ingestion run) — TTL is the backstop if this
    call is ever missed."""
    app = http_request.app
    count = await app.state.cache_store.invalidate_by_documents(
        tenant_id=body.tenant_id, document_ids=body.document_ids
    )
    return CacheInvalidateResponse(invalidated_count=count)
