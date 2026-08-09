"""§9's shared result shape: every guardrail check — input or output —
returns one of these, which becomes one row in `audit.guardrail_events`
(migration 0003) and one increment of `guardrail_events_total` (§12.2).
"""

from __future__ import annotations

from typing import Any, Literal

from prometheus_client import Counter
from pydantic import BaseModel

Stage = Literal["input", "output"]
Action = Literal["allow", "redact", "block", "flag"]

guardrail_events_total = Counter(
    "guardrail_events_total",
    "§12.2 — every guardrail check outcome",
    ["stage", "rule_id", "action"],
)


class GuardrailEvent(BaseModel):
    stage: Stage
    rule_id: str
    severity: str
    action_taken: Action
    detail: dict[str, Any] | None = None

    def record_metric(self) -> None:
        guardrail_events_total.labels(
            stage=self.stage, rule_id=self.rule_id, action=self.action_taken
        ).inc()
