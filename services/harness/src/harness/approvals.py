"""§8.1 `POST /v1/approvals/{approval_id}/decision`, harness half.
`api/routes.py`'s internal endpoint calls into this; gateway's public
endpoint (`services/gateway`) only authenticates and proxies — harness
owns `audit` (§5.6) so it's the one that reads/writes
`audit.mutation_requests` and decides authorization.

The approver-permission check reads `ToolManifestEntry.approver_permission_for`
(§22.2's `escalate_to_high_when.approver_permission`, now declared in
`config/tools/*.yaml` rather than M5's hardcoded stand-in).
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.common import MutationRequestStatus, RiskLevel
from harness.authz.manifest import ToolManifestEntry
from harness.clients.business_api import BusinessApiClient, BusinessApiError
from harness.persistence.repository import (
    get_mutation_request_by_approval_id,
    try_claim_mutation_decision,
    update_mutation_request_execution_result,
)
from sqlalchemy.ext.asyncio import AsyncConnection


class ApprovalError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@dataclass
class ApprovalDecisionResult:
    approval_id: str
    status: str  # "approved" | "rejected"
    mutation_status: str
    business_ref: str | None


async def decide_approval(
    conn: AsyncConnection,
    business_api: BusinessApiClient,
    *,
    trace_id: str,
    tenant_id: str,
    approval_id: str,
    approver_user_id: str,
    approver_permissions: list[str],
    decision: str,
    tool_manifests: dict[str, ToolManifestEntry],
) -> ApprovalDecisionResult:
    mutation = await get_mutation_request_by_approval_id(
        conn, tenant_id=tenant_id, approval_id=approval_id
    )
    if mutation is None:
        raise ApprovalError(404, "no mutation request found for this approval_id")

    tool = tool_manifests.get(mutation["action_name"])
    required_permission = (
        tool.approver_permission_for(RiskLevel(mutation["risk_level"])) if tool else None
    )
    if required_permission and required_permission not in approver_permissions:
        raise ApprovalError(403, f"approver lacks required permission: {required_permission}")
    if approver_user_id == mutation["actor_user_id"]:
        # Standard separation-of-duties rule — never explicitly demoed,
        # but leaving it out would mean any employee could "approve" their
        # own escalated request the moment they learn their own
        # approval_id, defeating the entire point of the approval step.
        raise ApprovalError(403, "an actor may not approve their own mutation request")

    new_status = "approved" if decision == "approve" else "rejected"
    won = await try_claim_mutation_decision(
        conn,
        tenant_id=tenant_id,
        approval_id=approval_id,
        new_status=new_status,
        approved_by=approver_user_id if decision == "approve" else None,
    )
    if not won:
        # §23.2i — someone else (or a retry of this same call) already
        # decided. Idempotent replay of the identical decision succeeds
        # quietly; a conflicting decision on an already-resolved request
        # is a real conflict.
        current_status = mutation["status"]
        already_matches = (
            decision == "approve" and current_status in ("approved", "executed")
        ) or (decision == "reject" and current_status == "rejected")
        if not already_matches:
            raise ApprovalError(409, f"mutation request already decided (status={current_status})")
        return ApprovalDecisionResult(
            approval_id=approval_id,
            status="approved" if decision == "approve" else "rejected",
            mutation_status=current_status,
            business_ref=mutation["business_ref"],
        )

    if decision == "reject":
        return ApprovalDecisionResult(
            approval_id=approval_id,
            status="rejected",
            mutation_status=MutationRequestStatus.REJECTED.value,
            business_ref=None,
        )

    assert tool is not None  # a mutation_request always names a real registered tool
    preview_payload = mutation["preview_payload"]
    try:
        result = await business_api.execute(
            domain=tool.domain,
            action=tool.business_action,
            preview_token=preview_payload["preview_token"],
            approval_id=approval_id,
            idempotency_key=preview_payload["idempotency_key"],
            tenant_id=tenant_id,
            actor_id=mutation["actor_user_id"],
            trace_id=trace_id,
        )
    except BusinessApiError as exc:
        await update_mutation_request_execution_result(
            conn, tenant_id=tenant_id, approval_id=approval_id, status="failed", business_ref=None
        )
        raise ApprovalError(502, f"execute failed: {exc.detail}") from exc

    await update_mutation_request_execution_result(
        conn,
        tenant_id=tenant_id,
        approval_id=approval_id,
        status="executed",
        business_ref=result.business_ref,
    )
    return ApprovalDecisionResult(
        approval_id=approval_id,
        status="approved",
        mutation_status="executed",
        business_ref=result.business_ref,
    )
