"""§9.2 format validity row: Pydantic-schema validation against the
output type. Today's `AgentOutput.content` is a plain string (no
structured/JSON output exists until tool-calling arrives at M5), so the
only thing that can actually be malformed is "empty" — the check is
intentionally thin now and is meant to grow real schema validation once
an agent can be asked for structured output.

Retry-once-then-block is orchestrated by `pipeline.py` (it needs the
model-router client and original messages to retry a call), not here —
this module only judges a single response.
"""

from __future__ import annotations

from harness.guardrails.events import GuardrailEvent


def is_valid_format(text: str) -> bool:
    return bool(text and text.strip())


def format_validity_event(valid: bool, *, retried: bool) -> GuardrailEvent:
    action = "allow" if valid else "block"
    return GuardrailEvent(
        stage="output",
        rule_id="format_validity",
        severity="info" if valid else "high",
        action_taken=action,  # type: ignore[arg-type]
        detail={"retried": retried},
    )
