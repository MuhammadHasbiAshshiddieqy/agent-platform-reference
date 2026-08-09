"""§9.2 groundedness row: LLM-as-judge (`agent-cheap`) compares the
answer's claims against the chunks actually retrieved. Spec text: `flag`
+ lower confidence always, `block` only "kalau agent mode strict" — no
agent-profile/strict-mode concept exists yet (§22.7 is M5b), so this
milestone implements the always-flag half only; a future strict mode
would call this same check and act on `action_taken == "flag"` itself
rather than needing a second groundedness implementation.

Skipped entirely (returns None) when there are no retrieved chunks —
there's nothing to ground a non-RAG answer against, and a judge asked to
score groundedness with empty context tends to return low, meaningless
scores rather than "not applicable".
"""

from __future__ import annotations

import re

from harness.clients.model_router import ModelRouterClient
from harness.guardrails.errors import GuardrailServiceError
from harness.guardrails.events import GuardrailEvent

FLAG_THRESHOLD = 0.5

_JUDGE_PROMPT_TEMPLATE = (
    "Anda adalah juri yang menilai apakah sebuah jawaban didukung oleh konteks "
    "yang diberikan. Nilai dengan SATU angka desimal antara 0.0 (jawaban tidak "
    "didukung sama sekali oleh konteks, kemungkinan halusinasi) dan 1.0 (setiap "
    "klaim di jawaban didukung penuh oleh konteks). Jawab HANYA dengan angka "
    "itu.\n\nKonteks:\n{context}\n\nJawaban:\n{answer}"
)

_FLOAT_RE = re.compile(r"[01]?\.\d+|[01]\b")


async def check_groundedness(
    answer: str, chunk_contents: list[str], model_router: ModelRouterClient
) -> GuardrailEvent | None:
    if not chunk_contents:
        return None

    context = "\n\n".join(chunk_contents)
    prompt = _JUDGE_PROMPT_TEMPLATE.format(context=context, answer=answer)
    try:
        # temperature=0.0 — a judge scoring groundedness should be
        # deterministic, not sampled; same rationale as injection.py/
        # offtopic.py's classifier calls.
        result = await model_router.chat(
            "agent-cheap", [{"role": "user", "content": prompt}], temperature=0.0
        )
    except Exception as exc:  # noqa: BLE001 — any failure here is a guardrail outage
        raise GuardrailServiceError(f"groundedness judge call failed: {exc}") from exc

    match = _FLOAT_RE.search(result.content or "")
    if not match:
        # Advisory-only check (never blocks in this milestone) — an
        # unparseable judge response just means no signal, not a failure.
        return None
    score = max(0.0, min(1.0, float(match.group())))

    action = "flag" if score < FLAG_THRESHOLD else "allow"
    return GuardrailEvent(
        stage="output",
        rule_id="groundedness",
        severity="medium" if action == "flag" else "info",
        action_taken=action,  # type: ignore[arg-type]
        detail={"score": score},
    )
