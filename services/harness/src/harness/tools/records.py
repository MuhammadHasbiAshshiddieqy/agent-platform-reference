"""Shapes collected in `AgentState` during a run and persisted by
`graph/runner.py` in its existing `tenant_session` block once the graph
finishes — mirrors §9's `GuardrailEvent` pattern (guardrails/events.py):
graph nodes never touch the DB directly, only `runner.py` does.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ToolInvocationRecord(BaseModel):
    """One row in `audit.tool_invocations` (migration 0003)."""

    tool_name: str
    tool_kind: str  # "readonly" | "mutation"
    arguments: dict[str, Any]
    result_summary: dict[str, Any] | None
    status: str  # "ok" | "error"


class MutationRequestRecord(BaseModel):
    """One row in `audit.mutation_requests` (migration 0003). Only the
    *initial* insert made from the agent run — the approval decision
    endpoint (`api/routes.py`) updates this same row later via its own
    race-safe `UPDATE ... WHERE status = 'awaiting_approval'` (§23.2i),
    a completely separate request from the one that created it.
    """

    id: str
    action_name: str
    risk_level: str
    preview_payload: dict[str, Any]
    approval_id: str | None
    idempotency_key: str
    status: str  # audit.mutation_requests.status values (contracts.common.MutationRequestStatus)
    business_ref: str | None
    executed_at: datetime | None  # set only when executed immediately (no approval needed)
