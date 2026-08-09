"""§22.2's tool manifest — one YAML file per tool in `config/tools/`,
loaded once at boot (`main.py`'s lifespan) and held for the process
lifetime. `load_tool_manifests` is also §21/ADR-011's boot-validation
mechanism: an audience with zero matching tools after the filter is a
deployment misconfiguration (someone pointed `harness-external` at a
manifest set with no `external`-audience tool in it), not a valid empty
state — it raises rather than silently booting with nothing to offer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from contracts.common import Audience, DataScope, RiskLevel, ToolKind
from contracts.tools import (
    AdjustPayrollParams,
    GetLeaveBalanceParams,
    SearchPublicFaqParams,
    SubmitLeaveRequestParams,
)
from pydantic import BaseModel, Field

# §22.2 `parameters_schema` is a string naming a Pydantic model in
# `packages/contracts` — this is the resolution table. A tool manifest
# whose `parameters_schema` isn't here fails to load loudly (pydantic
# validation error), not silently with no parameters.
PARAMS_MODELS: dict[str, type[BaseModel]] = {
    "GetLeaveBalanceParams": GetLeaveBalanceParams,
    "SubmitLeaveRequestParams": SubmitLeaveRequestParams,
    "AdjustPayrollParams": AdjustPayrollParams,
    "SearchPublicFaqParams": SearchPublicFaqParams,
}


class ManifestLoadError(RuntimeError):
    """Raised at boot — never caught and turned into a degraded mode."""


class EscalationRule(BaseModel):
    condition: str
    approver_permission: str


class RateLimit(BaseModel):
    per_user_per_hour: int
    per_tenant_per_hour: int


class ToolManifestEntry(BaseModel):
    name: str
    version: int
    kind: ToolKind
    audience: list[Audience]
    risk_level: RiskLevel

    description_for_model: str
    parameters_schema: str
    # Not in §22.2's literal YAML sample — added because `business_action`
    # alone doesn't say which business-api URL prefix to call
    # (`/hr/v1/...` vs `/payroll/v1/...`); `domain: faq` is a further
    # local convention meaning "route to retrieval-service, not
    # business-api at all" (see `tools/executor.py`).
    domain: str
    business_action: str

    required_permissions: list[str] = Field(default_factory=list)
    data_scope: DataScope
    scope_param: str | None = None

    required_scopes_for_token_exchange: list[str] = Field(default_factory=list)
    rate_limit: RateLimit | None = None
    escalate_to_high_when: list[EscalationRule] = Field(default_factory=list)
    # Top-level, distinct from escalate_to_high_when's per-rule version —
    # covers tools that are statically `risk_level: high` (adjust_payroll)
    # with no conditional escalation rule to hang the approver on.
    approver_permission: str | None = None

    cacheable: bool = False
    audit_level: str = "full"

    @property
    def parameters_model(self) -> type[BaseModel]:
        return PARAMS_MODELS[self.parameters_schema]

    def approver_permission_for(self, risk_level: RiskLevel) -> str | None:
        """The permission an approver needs, for whichever way this tool
        reached `risk_level: high` — statically or via an escalation
        rule. Both `submit_leave_request` (conditional) and
        `adjust_payroll` (static) resolve through this one method so
        `approvals.py` doesn't need to know which shape a given tool used.
        """
        if risk_level != RiskLevel.HIGH:
            return None
        if self.approver_permission:
            return self.approver_permission
        if self.escalate_to_high_when:
            return self.escalate_to_high_when[0].approver_permission
        return None


def load_tool_manifests(tools_dir: Path, *, audience: Audience) -> dict[str, ToolManifestEntry]:
    entries: dict[str, ToolManifestEntry] = {}
    for path in sorted(tools_dir.glob("*.yaml")):
        raw: dict[str, Any] = yaml.safe_load(path.read_text())
        entry = ToolManifestEntry.model_validate(raw)
        if audience not in entry.audience:
            continue
        entries[entry.name] = entry

    if not entries:
        raise ManifestLoadError(
            f"no tool manifest under {tools_dir} declares audience={audience.value} — "
            "a deployment with zero available tools is a misconfiguration, not a valid "
            "empty state (§21/ADR-011)"
        )
    return entries
