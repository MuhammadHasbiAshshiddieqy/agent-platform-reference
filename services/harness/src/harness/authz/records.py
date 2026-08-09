"""§22.8's `audit.authz_decisions` row shape + the metrics counters
tracked alongside it. Mirrors `harness.guardrails.events.GuardrailEvent`'s
pattern: one record per decision, persisted by `graph/runner.py`,
counted here.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge
from pydantic import BaseModel, Field

authz_decisions_total = Counter(
    "authz_decisions_total",
    "§22.8 — every tool authorization decision",
    ["decision", "tool_name", "audience", "agent_id"],
)
authz_denied_by_reason_total = Counter(
    "authz_denied_by_reason_total",
    "§22.8 — denials by reason, to spot misconfiguration vs. genuine over-reach attempts",
    ["reason"],
)
tool_registry_size = Gauge(
    "tool_registry_size",
    "§22.8 — how many tools are in an agent's designed ceiling for its audience",
    ["agent_id", "audience"],
)


class AuthzDecisionRecord(BaseModel):
    tool_name: str
    decision: str  # "allow" | "deny"
    deny_reason: str | None = None
    missing_permissions: list[str] = Field(default_factory=list)
    data_scope: str | None = None
    scope_satisfied: bool | None = None

    def record_metric(self, *, audience: str, agent_id: str) -> None:
        authz_decisions_total.labels(
            decision=self.decision, tool_name=self.tool_name, audience=audience, agent_id=agent_id
        ).inc()
        if self.decision == "deny" and self.deny_reason:
            authz_denied_by_reason_total.labels(reason=self.deny_reason).inc()
