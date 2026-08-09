"""§22.2's `parameters_schema` field — one Pydantic model per tool, used
both to generate the JSON schema sent to model-router's tool-calling API
(harness `tools/registry.py`) and to validate preview/execute params on
the business-api side (`mock-business-api` — never trust the caller,
§8.4). The three actions match §24.2's domain seed exactly.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from contracts.common import StrictModel


class GetLeaveBalanceParams(StrictModel):
    employee_id: str


class SubmitLeaveRequestParams(StrictModel):
    employee_id: str
    leave_days: int = Field(gt=0)
    start_date: date


class AdjustPayrollParams(StrictModel):
    employee_id: str
    adjustment_percent: float
    reason: str


class SearchPublicFaqParams(StrictModel):
    """§21's one deliberately `audience: [external, internal]` tool
    (ADR-011) — proves the audience filter actually excludes something,
    not just that it exists. Queries retrieval-service directly (not the
    automatic RAG `retrieve` node), forced to `grp_public` regardless of
    the caller's real ACL groups — this tool's whole point is that its
    answer is safe for an audience with no HRIS access at all."""

    query: str
