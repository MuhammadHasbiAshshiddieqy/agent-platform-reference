"""§9.2 policy violation row: YAML-defined keyword rules, loaded once at
import time from `policies/output_policy.yaml` (versioned with the code
per the spec's own instruction). `check_policy` is pure string matching —
no model call, so it can never raise `GuardrailServiceError`; a malformed
policy file is a deploy-time bug, caught by `load_policy_rules` at import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from harness.guardrails.events import GuardrailEvent
from pydantic import BaseModel

_POLICY_PATH = Path(__file__).parent / "policies" / "output_policy.yaml"


class PolicyRule(BaseModel):
    rule_id: str
    severity: str
    action: Literal["allow", "redact", "block", "flag"]
    keywords: list[str]


def load_policy_rules(path: Path = _POLICY_PATH) -> list[PolicyRule]:
    data = yaml.safe_load(path.read_text())
    return [PolicyRule.model_validate(r) for r in data["rules"]]


_RULES = load_policy_rules()


def check_policy(text: str, rules: list[PolicyRule] = _RULES) -> GuardrailEvent | None:
    lowered = text.lower()
    for rule in rules:
        for keyword in rule.keywords:
            if keyword.lower() in lowered:
                return GuardrailEvent(
                    stage="output",
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    action_taken=rule.action,
                    detail={"matched_keyword": keyword},
                )
    return None
