"""§9.1 off-topic row: classify the input against the agent's own
description, `block` (with a friendly message) if it's clearly outside
scope. No agent-profile system exists yet (§22.7 is M5b) — a small static
map stands in for it until then.

**Unregistered `agent_id`s skip this check entirely (see `check_offtopic`'s
early return) rather than falling back to a generic description.**
Originally this fell back to the HR-assistant description for any unknown
`agent_id`, on the theory that M1-M3's test agent ids (`m1-smoke-test`,
`m3-rag-test`, ...) "were never meant to be real agents" so it wouldn't
matter. In practice that made the classifier correctly-but-uselessly
block those tests' deliberately domain-agnostic smoke prompts ("Reply
with exactly one word: hello.") as off-topic *for HR* — which they are,
but that was never the scope they were meant to be judged against. There
is no way to judge "in scope" without knowing the scope; guessing one
produces exactly this false-positive class. Skipping is the honest
answer until a real per-agent profile exists.
"""

from __future__ import annotations

import re

from harness.clients.model_router import ModelRouterClient
from harness.guardrails.errors import GuardrailServiceError
from harness.guardrails.events import GuardrailEvent

BLOCK_THRESHOLD = 0.85

AGENT_DESCRIPTIONS: dict[str, str] = {
    "hr-assistant": (
        "Asisten HR internal untuk membantu karyawan dengan pertanyaan seputar cuti, lembur, "
        "reimbursement, kebijakan HR, administrasi kepegawaian, penyesuaian payroll, dan "
        "struktur organisasi."
    ),
}


# Positive framing ("does this match, answer 1/0"), not "rate how far
# out of scope this is on a 0.0-1.0 scale" — empirically, the negated/
# graded version made `agent-local`'s qwen2.5:3b fallback (the model this
# runs against whenever GEMINI_API_KEY is empty, e.g. this dev machine)
# answer "1.0" (= "out of scope") for *every* input tried, including
# textbook on-topic HR questions — a systematic double-negation failure,
# not noise. The binary yes/no phrasing below fixed that in manual
# testing against the live stack; it still isn't perfectly reliable on a
# 3B CPU model (occasional false negatives remain — see CLAUDE.md), but a
# real `agent-cheap` (gemini-3.5-flash-lite) should have no trouble with
# either phrasing.
_CLASSIFIER_PROMPT_TEMPLATE = (
    "Topik assistant ini: {description}\n\n"
    "Apakah pertanyaan berikut membahas salah satu topik itu? Jawab HANYA dengan "
    "angka 1 jika ya (sesuai topik), atau 0 jika tidak (di luar topik). Jangan "
    "jawab apapun selain angka itu.\n\nPertanyaan: {text}"
)

_ON_TOPIC_RE = re.compile(r"[01]\b")


async def check_offtopic(
    text: str, agent_id: str, model_router: ModelRouterClient
) -> GuardrailEvent | None:
    description = AGENT_DESCRIPTIONS.get(agent_id)
    if description is None:
        return None

    prompt = _CLASSIFIER_PROMPT_TEMPLATE.format(description=description, text=text)
    try:
        # temperature=0.0 — sampling variance on a binary classifier means
        # the identical input can flip allow/block between runs; found via
        # live reproduction on this dev machine's `agent-local` fallback.
        result = await model_router.chat(
            "agent-cheap", [{"role": "user", "content": prompt}], temperature=0.0
        )
    except Exception as exc:  # noqa: BLE001 — any failure here is a guardrail outage
        raise GuardrailServiceError(f"offtopic classifier call failed: {exc}") from exc

    match = _ON_TOPIC_RE.search(result.content or "")
    # Unparseable classifier output defaults to "in scope" — offtopic has
    # no deterministic backstop the way injection does, and blocking a
    # legitimate question because a weak local model returned garbled
    # text would be a worse failure mode than the reverse (see
    # injection.py's docstring for the same reasoning applied there).
    # `score` here is "how off-topic", kept in the same 0.0-1.0 direction
    # as every other guardrail's score even though the model itself only
    # ever returns a bare 0/1.
    on_topic = match.group() == "1" if match else True
    score = 0.0 if on_topic else 1.0

    action = "block" if score >= BLOCK_THRESHOLD else "allow"
    return GuardrailEvent(
        stage="input",
        rule_id="off_topic",
        severity="medium" if action == "block" else "info",
        action_taken=action,  # type: ignore[arg-type]
        detail={"score": score, "parsed": match is not None},
    )
