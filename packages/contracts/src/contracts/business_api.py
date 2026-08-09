"""§8.4 — business-api mutation contract. Two-phase preview → execute.

`execute` accepts only a `preview_token`, never raw params (§8.4) — this is
what stops an agent from changing arguments between preview and commit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from contracts.common import RiskLevel, StrictModel


class MutationEffect(StrictModel):
    """One entry in PreviewResponse.effects — a human-readable diff of what execute would do."""

    resource: str
    id: str | None = None
    operation: str | None = None
    from_: Any = Field(default=None, alias="from")
    to: Any = None


class PreviewRequest(StrictModel):
    params: dict[str, Any]


class PreviewResponse(StrictModel):
    action: str
    risk_level: RiskLevel
    requires_approval: bool
    effects: list[MutationEffect] = Field(default_factory=list)
    reversible: bool
    preview_token: str
    validation_errors: list[str] = Field(default_factory=list)


class ExecuteRequest(StrictModel):
    preview_token: str
    approval_id: str | None = None  # required by the adapter when requires_approval was true


class ExecuteResponse(StrictModel):
    action: str
    status: str
    business_ref: str | None = None
    executed_at: datetime | None = None
