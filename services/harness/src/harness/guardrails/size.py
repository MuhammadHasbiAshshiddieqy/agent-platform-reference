"""§9.1 input size row: deterministic token counter, `block` above the
limit. Same ~4-chars/token heuristic used by
`services/gateway/src/gateway/quota.py` and `services/retrieval/src/
retrieval/service.py` — no service imports another (§4.1 rule 1), so each
keeps its own copy rather than sharing one through `contracts`.
"""

from __future__ import annotations

from harness.guardrails.events import GuardrailEvent

CHARS_PER_TOKEN = 4
MAX_INPUT_TOKENS = 8000


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def check_input_size(text: str, max_tokens: int = MAX_INPUT_TOKENS) -> GuardrailEvent:
    estimated = estimate_tokens(text)
    action = "block" if estimated > max_tokens else "allow"
    return GuardrailEvent(
        stage="input",
        rule_id="input_size",
        severity="low" if action == "block" else "info",
        action_taken=action,  # type: ignore[arg-type]
        detail={"estimated_tokens": estimated, "max_tokens": max_tokens},
    )
