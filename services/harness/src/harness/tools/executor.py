"""§5.3's `act`: executes one model-requested tool call. Readonly tools
call business-api (or, for `search_public_faq`, retrieval-service) once
and hand the result back. Mutation tools go through §8.4's preview ->
(approval branch) -> execute — exactly what a human clicking through a UI
would trigger, never a shortcut around it.

§22.4's "pengecekan kedua saat eksekusi" lives here: `allowed_tools` and
`scope_constraints` are the *same* `PolicyDecision` the `authorize` node
computed once at the start of the run (`graph/state.py`), passed straight
through — this function never recomputes policy, it only re-checks the
model's actual tool call against what was already decided.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from contracts.agent import PendingApproval
from contracts.authz import ScopeConstraint
from contracts.common import ToolKind
from contracts.retrieval import SearchRequest
from harness.authz.manifest import ToolManifestEntry
from harness.authz.scope_check import apply_scope_default, check_scope
from harness.clients.business_api import BusinessApiClient, BusinessApiError
from harness.clients.retrieval import RetrievalClient
from harness.clients.token_exchange import TokenExchangeClient, TokenExchangeError
from harness.graph.state import ToolCallState
from harness.tools.records import MutationRequestRecord, ToolInvocationRecord
from pydantic import ValidationError


class ToolExecutionOutcome:
    def __init__(
        self,
        *,
        tool_call_id: str,
        content: str,
        invocation: ToolInvocationRecord,
        mutation_previewed: str | None = None,
        mutation_executed: str | None = None,
        mutation_request: MutationRequestRecord | None = None,
        pending_approval: PendingApproval | None = None,
    ) -> None:
        self.tool_call_id = tool_call_id
        self.content = content
        self.invocation = invocation
        self.mutation_previewed = mutation_previewed
        self.mutation_executed = mutation_executed
        self.mutation_request = mutation_request
        self.pending_approval = pending_approval


def _error_outcome(
    tool_call: ToolCallState, tool_name: str, tool_kind: str, message: str
) -> ToolExecutionOutcome:
    return ToolExecutionOutcome(
        tool_call_id=tool_call.id,
        content=json.dumps({"error": message}),
        invocation=ToolInvocationRecord(
            tool_name=tool_name,
            tool_kind=tool_kind,
            arguments=tool_call.arguments,
            result_summary=None,
            status="error",
        ),
    )


async def execute_tool_call(
    tool_call: ToolCallState,
    *,
    tenant_id: str,
    user_id: str,
    employee_id: str | None,
    trace_id: str,
    subject_token: str | None,
    tool_manifests: dict[str, ToolManifestEntry],
    allowed_tools: list[str],
    scope_constraints: dict[str, ScopeConstraint],
    business_api: BusinessApiClient,
    retrieval: RetrievalClient,
    token_exchange: TokenExchangeClient,
) -> ToolExecutionOutcome:
    tool = tool_manifests.get(tool_call.name)
    if tool is None:
        return _error_outcome(
            tool_call, tool_call.name, "unknown", f"unknown tool: {tool_call.name}"
        )

    if tool.name not in allowed_tools:
        # §22.4's second check — this tool wasn't in the schema offered to
        # the model for this run (wrong permission, killswitch, mutation
        # not allowed, ...); the model calling it anyway (hallucination or
        # a stale/edited message list) must not fall through to execution.
        return _error_outcome(
            tool_call, tool.name, tool.kind.value, "tool not authorized for this run"
        )

    raw_args: dict[str, Any] = dict(tool_call.arguments)
    if tool.scope_param:
        # Model omitted it -> default to the caller's own id (usability).
        # Model passed an explicit value outside the resolved scope ->
        # reject outright, don't silently substitute (§22.9 test class 3;
        # see scope_check.py's docstring for why this replaced M5's
        # simpler unconditional-force behavior).
        apply_scope_default(raw_args, scope_param=tool.scope_param, employee_id=employee_id)
        constraint = scope_constraints.get(tool.name)
        if constraint is not None:
            denial_reason = check_scope(
                raw_args, scope_param=tool.scope_param, constraint=constraint
            )
            if denial_reason:
                return _error_outcome(tool_call, tool.name, tool.kind.value, denial_reason)

    try:
        params = tool.parameters_model.model_validate(raw_args)
    except ValidationError as exc:
        return _error_outcome(tool_call, tool.name, tool.kind.value, f"invalid arguments: {exc}")

    if tool.domain == "faq":
        return await _execute_search_public_faq(
            tool_call, tool, params.model_dump(mode="json"), retrieval, tenant_id, trace_id
        )

    if tool.kind == ToolKind.READONLY:
        return await _execute_readonly(
            tool_call,
            tool,
            params.model_dump(mode="json"),
            business_api,
            tenant_id,
            user_id,
            trace_id,
        )
    return await _execute_mutation(
        tool_call,
        tool,
        params.model_dump(mode="json"),
        business_api,
        token_exchange,
        subject_token,
        tenant_id,
        user_id,
        trace_id,
    )


async def _exchange_token_for(
    tool: ToolManifestEntry,
    token_exchange: TokenExchangeClient,
    subject_token: str | None,
) -> str | None:
    if not tool.required_scopes_for_token_exchange:
        return None
    if not subject_token:
        # No subject_token to exchange from (shouldn't happen on the live
        # path — gateway always forwards one) -> proceed without a token;
        # business-api's independent check on its side is the real gate,
        # this just means the extra defense-in-depth layer is skipped.
        return None
    try:
        return await token_exchange.exchange(
            subject_token=subject_token,
            audience="business-api",
            scope=" ".join(tool.required_scopes_for_token_exchange),
        )
    except TokenExchangeError:
        return None


async def _execute_search_public_faq(
    tool_call: ToolCallState,
    tool: ToolManifestEntry,
    params: dict[str, Any],
    retrieval: RetrievalClient,
    tenant_id: str,
    trace_id: str,
) -> ToolExecutionOutcome:
    # Forced to grp_public regardless of the caller's real ACL groups —
    # this tool's entire point is that its answer is safe for an audience
    # with no HRIS/internal-corpus access at all (§21.2's external row:
    # "hanya chunk ber-ACL publik").
    result = await retrieval.search(
        SearchRequest(
            trace_id=trace_id,
            tenant_id=tenant_id,
            acl_group_ids=["grp_public"],
            query=str(params["query"]),
            top_k=3,
        )
    )
    chunks = [{"content": c.content, "source_uri": c.source_uri} for c in result.chunks]
    summary = {"chunks": chunks}
    return ToolExecutionOutcome(
        tool_call_id=tool_call.id,
        content=json.dumps(summary),
        invocation=ToolInvocationRecord(
            tool_name=tool.name,
            tool_kind="readonly",
            arguments=params,
            result_summary=summary,
            status="ok",
        ),
    )


async def _execute_readonly(
    tool_call: ToolCallState,
    tool: ToolManifestEntry,
    params: dict[str, Any],
    business_api: BusinessApiClient,
    tenant_id: str,
    user_id: str,
    trace_id: str,
) -> ToolExecutionOutcome:
    try:
        result = await business_api.query(
            domain=tool.domain,
            action=tool.business_action,
            params=params,
            tenant_id=tenant_id,
            actor_id=user_id,
            trace_id=trace_id,
        )
    except BusinessApiError as exc:
        return _error_outcome(tool_call, tool.name, "readonly", exc.detail)

    return ToolExecutionOutcome(
        tool_call_id=tool_call.id,
        content=json.dumps(result),
        invocation=ToolInvocationRecord(
            tool_name=tool.name,
            tool_kind="readonly",
            arguments=params,
            result_summary=result,
            status="ok",
        ),
    )


async def _execute_mutation(
    tool_call: ToolCallState,
    tool: ToolManifestEntry,
    params: dict[str, Any],
    business_api: BusinessApiClient,
    token_exchange: TokenExchangeClient,
    subject_token: str | None,
    tenant_id: str,
    user_id: str,
    trace_id: str,
) -> ToolExecutionOutcome:
    access_token = await _exchange_token_for(tool, token_exchange, subject_token)
    try:
        preview = await business_api.preview(
            domain=tool.domain,
            action=tool.business_action,
            params=params,
            tenant_id=tenant_id,
            actor_id=user_id,
            trace_id=trace_id,
            access_token=access_token,
        )
    except BusinessApiError as exc:
        return _error_outcome(tool_call, tool.name, "mutation", exc.detail)

    if preview.validation_errors:
        return _error_outcome(
            tool_call, tool.name, "mutation", "; ".join(preview.validation_errors)
        )

    mrq_id = f"mrq_{uuid.uuid4().hex[:20]}"
    idem_key = f"exec_{uuid.uuid4().hex[:20]}"
    preview_payload = {"params": params, "preview_token": preview.preview_token}

    if preview.requires_approval:
        approval_id = f"apr_{uuid.uuid4().hex[:20]}"
        mutation_request = MutationRequestRecord(
            id=mrq_id,
            action_name=tool.name,
            risk_level=preview.risk_level.value,
            preview_payload={**preview_payload, "idempotency_key": idem_key},
            approval_id=approval_id,
            idempotency_key=idem_key,
            status="awaiting_approval",
            business_ref=None,
            executed_at=None,
        )
        pending_approval = PendingApproval(
            approval_id=approval_id, action_name=tool.name, risk_level="high"
        )
        content = json.dumps(
            {
                "status": "awaiting_approval",
                "message": f"Aksi {tool.name} memerlukan persetujuan sebelum dapat dieksekusi.",
                "approval_id": approval_id,
            }
        )
        return ToolExecutionOutcome(
            tool_call_id=tool_call.id,
            content=content,
            invocation=ToolInvocationRecord(
                tool_name=tool.name,
                tool_kind="mutation",
                arguments=params,
                result_summary={"requires_approval": True},
                status="ok",
            ),
            mutation_previewed=tool.name,
            mutation_request=mutation_request,
            pending_approval=pending_approval,
        )

    # medium risk, no approval needed — execute immediately (§8.4's rule
    # table: requires `options.allow_mutations = true`, already checked
    # by the caller before this function is ever reached for a mutation).
    try:
        result = await business_api.execute(
            domain=tool.domain,
            action=tool.business_action,
            preview_token=preview.preview_token,
            approval_id=None,
            idempotency_key=idem_key,
            tenant_id=tenant_id,
            actor_id=user_id,
            trace_id=trace_id,
        )
    except BusinessApiError as exc:
        mutation_request = MutationRequestRecord(
            id=mrq_id,
            action_name=tool.name,
            risk_level=preview.risk_level.value,
            preview_payload={**preview_payload, "idempotency_key": idem_key},
            approval_id=None,
            idempotency_key=idem_key,
            status="failed",
            business_ref=None,
            executed_at=None,
        )
        return ToolExecutionOutcome(
            tool_call_id=tool_call.id,
            content=json.dumps({"error": exc.detail}),
            invocation=ToolInvocationRecord(
                tool_name=tool.name,
                tool_kind="mutation",
                arguments=params,
                result_summary=None,
                status="error",
            ),
            mutation_previewed=tool.name,
            mutation_request=mutation_request,
        )

    mutation_request = MutationRequestRecord(
        id=mrq_id,
        action_name=tool.name,
        risk_level=preview.risk_level.value,
        preview_payload={**preview_payload, "idempotency_key": idem_key},
        approval_id=None,
        idempotency_key=idem_key,
        status="executed",
        business_ref=result.business_ref,
        executed_at=result.executed_at,
    )
    content = json.dumps({"status": "executed", "business_ref": result.business_ref})
    return ToolExecutionOutcome(
        tool_call_id=tool_call.id,
        content=content,
        invocation=ToolInvocationRecord(
            tool_name=tool.name,
            tool_kind="mutation",
            arguments=params,
            result_summary={"business_ref": result.business_ref},
            status="ok",
        ),
        mutation_previewed=tool.name,
        mutation_executed=tool.name,
        mutation_request=mutation_request,
    )
