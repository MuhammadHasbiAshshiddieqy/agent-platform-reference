"""§8.2 — agent-harness internal API. Gateway and async-worker both call
`POST /internal/v1/runs` with this exact shape; the harness does not care
which of the two callers it was."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from contracts.agent import AgentInput, RunOptions, TokenBudget
from contracts.common import ExecutionMode, StrictModel


class AgentRunRequest(StrictModel):
    run_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    acl_group_ids: list[str] = Field(default_factory=list)
    # §22.3's full JWT claim shape. `employee_id`/`permissions` arrived in
    # M5 ahead of the PolicyResolver that actually needed them (self-scope
    # forcing, approver-permission checks); `roles`/`scope_context` are
    # M5b's own addition, feeding `contracts.authz.AuthorizationContext`.
    employee_id: str | None = None
    permissions: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    scope_context: dict[str, Any] = Field(default_factory=dict)
    # The caller's own bearer JWT, forwarded verbatim so harness can use it
    # as §22.5's RFC 8693 `subject_token` when a tool requires token
    # exchange. Never logged, never persisted, never enters a Langfuse
    # trace (§22.5) — passed through in memory only, for the duration of
    # one tool call's business-api request.
    subject_token: str | None = None
    agent_id: str
    conversation_id: str | None = None
    input: AgentInput
    context: dict[str, Any] = Field(default_factory=dict)
    options: RunOptions
    execution_mode: ExecutionMode
    budget: TokenBudget
    # §6 L3 / §5.10 — async-worker's own LiteLLM virtual key (`async-pool`,
    # separate daily USD budget from the sync path's). `None` for every
    # sync request; async-worker sets this on its own copy of the
    # `AgentRunRequest` it received (built by gateway, which doesn't
    # provision this key) right before calling harness — see
    # services/async-worker/src/async_worker/processor.py.
    model_router_key_override: str | None = None
    # §13.1 — gateway forwards the raw `X-Eval-Mode` header presence
    # unconditionally; harness alone decides whether to honor it
    # (`tenant_id ∈ EVAL_TENANT_IDS`, its own setting) and is the only
    # party that can write the `eval_mode_abuse` audit event if it's
    # requested for an ineligible tenant — never trust the caller's own
    # claim about its eligibility (§8.4), even though gateway already
    # resolved this same tenant_id from the JWT one hop earlier.
    eval_mode_requested: bool = False


class ApprovalDecisionInternalRequest(StrictModel):
    """POST /internal/v1/approvals/{approval_id}/decision — gateway forwards
    the approver's already-authenticated identity; harness owns `audit`
    (§5.6) so it's the one that reads/writes `audit.mutation_requests` and
    decides authorization (§23.2i's race-safe UPDATE...WHERE pattern)."""

    trace_id: str
    tenant_id: str
    approver_user_id: str
    approver_permissions: list[str] = Field(default_factory=list)
    decision: Literal["approve", "reject"]


class ApprovalDecisionInternalResponse(StrictModel):
    approval_id: str
    status: Literal["approved", "rejected"]
    mutation_status: str
    business_ref: str | None = None


class KillswitchInternalRequest(StrictModel):
    """POST /internal/v1/admin/killswitch/{tools|agents}/{name} — gateway
    forwards the already-authenticated admin's identity (same boundary-#2
    pattern as ApprovalDecisionInternalRequest)."""

    disabled: bool
    reason: str | None = None
    changed_by: str


class CacheInvalidateRequest(StrictModel):
    """POST /internal/v1/cache/invalidate — §10's document-changed event.
    Called directly by ingestion-service (no Kong route, no gateway
    proxy — this is service-to-service, like ingestion/retrieval's own
    `/internal/v1/...` endpoints, not a public-facing action)."""

    tenant_id: str
    document_ids: list[str] = Field(default_factory=list)


class CacheInvalidateResponse(StrictModel):
    invalidated_count: int
