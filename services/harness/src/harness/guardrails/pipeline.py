"""Orchestrates the two guardrail stages (§9) into the shapes
`graph/build.py`'s nodes need. Each stage function is a plain async
function, not a LangGraph node itself, so it stays testable without
constructing a graph.

**Fail-closed boundary:** every `GuardrailServiceError` raised by a check
this module calls is left to propagate — deliberately not caught here.
`graph/build.py`'s nodes don't catch it either; it reaches `run_agent`
and then `api/routes.py`, which is the one place that turns it into an
HTTP 503 (§9.2's hard rule). Catching and "handling" it anywhere in
between would silently turn a fail-closed guarantee into a fail-open bug.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from harness.clients.model_router import ModelRouterClient
from harness.guardrails import injection, offtopic, pii, policy, size
from harness.guardrails.events import GuardrailEvent
from harness.guardrails.format_validity import format_validity_event, is_valid_format
from harness.guardrails.groundedness import check_groundedness

REFUSAL_MESSAGES = {
    "input_size": "Maaf, pertanyaan Anda terlalu panjang. Mohon coba pecah menjadi beberapa "
    "pertanyaan yang lebih singkat.",
    "prompt_injection": "Maaf, saya tidak dapat memproses permintaan ini.",
    "off_topic": "Maaf, pertanyaan ini di luar cakupan yang bisa saya bantu. Silakan hubungi "
    "tim terkait untuk bantuan lebih lanjut.",
    "policy_violation": "Maaf, saya tidak dapat memberikan pernyataan semacam itu. Untuk hal "
    "ini, silakan hubungi tim terkait secara langsung.",
    "format_validity": "Maaf, terjadi kendala saat menyusun jawaban. Silakan coba lagi atau "
    "hubungi tim terkait.",
}


class InputGuardrailResult:
    def __init__(
        self,
        *,
        text: str,
        pii_mapping: dict[str, str],
        refused: bool,
        refusal_reason: str | None,
        events: list[GuardrailEvent],
    ) -> None:
        self.text = text
        self.pii_mapping = pii_mapping
        self.refused = refused
        self.refusal_reason = refusal_reason
        self.events = events


async def run_input_guardrails(
    input_text: str, agent_id: str, model_router: ModelRouterClient
) -> InputGuardrailResult:
    events: list[GuardrailEvent] = []

    # Cheapest, purely local check first — no reason to redact PII or
    # spend an LLM call classifying a message we're about to reject.
    size_event = size.check_input_size(input_text)
    events.append(size_event)
    if size_event.action_taken == "block":
        return InputGuardrailResult(
            text=input_text,
            pii_mapping={},
            refused=True,
            refusal_reason="input_size",
            events=events,
        )

    redacted_text, pii_mapping, pii_event = pii.redact_input_pii(input_text)
    if pii_event:
        events.append(pii_event)

    injection_event = await injection.check_input_injection(redacted_text, model_router)
    events.append(injection_event)
    if injection_event.action_taken == "block":
        return InputGuardrailResult(
            text=redacted_text,
            pii_mapping=pii_mapping,
            refused=True,
            refusal_reason="prompt_injection",
            events=events,
        )

    # None for agent_ids with no registered description (see
    # offtopic.py's module docstring) — nothing to check against.
    offtopic_event = await offtopic.check_offtopic(redacted_text, agent_id, model_router)
    if offtopic_event:
        events.append(offtopic_event)
    if offtopic_event and offtopic_event.action_taken == "block":
        return InputGuardrailResult(
            text=redacted_text,
            pii_mapping=pii_mapping,
            refused=True,
            refusal_reason="off_topic",
            events=events,
        )

    return InputGuardrailResult(
        text=redacted_text,
        pii_mapping=pii_mapping,
        refused=False,
        refusal_reason=None,
        events=events,
    )


def scan_retrieved_chunks(
    chunks: list[tuple[str, str]],
) -> tuple[set[str], list[GuardrailEvent]]:
    """§9.1: "konten dari RAG juga harus lewat pemeriksaan injection" —
    `chunks` is `[(chunk_id, content), ...]`. Returns the set of chunk_ids
    to drop from the prompt (not a whole-request refusal — one poisoned
    document shouldn't take down every RAG answer, see
    `injection.scan_chunk_for_injection`'s docstring) plus the events for
    whichever chunks were flagged.
    """
    blocked_ids: set[str] = set()
    events: list[GuardrailEvent] = []
    for chunk_id, content in chunks:
        event = injection.scan_chunk_for_injection(chunk_id, content)
        if event:
            events.append(event)
            blocked_ids.add(chunk_id)
    return blocked_ids, events


class OutputGuardrailResult:
    def __init__(
        self, *, text: str, refused: bool, refusal_reason: str | None, events: list[GuardrailEvent]
    ) -> None:
        self.text = text
        self.refused = refused
        self.refusal_reason = refusal_reason
        self.events = events


async def run_output_guardrails(
    *,
    output_text: str,
    chunk_contents: list[str],
    pii_mapping: dict[str, str],
    model_router: ModelRouterClient,
    regenerate: Callable[[], Awaitable[str]],
) -> OutputGuardrailResult:
    events: list[GuardrailEvent] = []

    # Format validity first, with its own retry — every later check
    # should judge the text we're actually going to keep, not a draft
    # that's about to be thrown away.
    valid = is_valid_format(output_text)
    retried = False
    if not valid:
        output_text = await regenerate()
        retried = True
        valid = is_valid_format(output_text)
    events.append(format_validity_event(valid, retried=retried))
    if not valid:
        return OutputGuardrailResult(
            text=REFUSAL_MESSAGES["format_validity"],
            refused=True,
            refusal_reason="format_validity",
            events=events,
        )

    groundedness_event = await check_groundedness(output_text, chunk_contents, model_router)
    if groundedness_event:
        events.append(groundedness_event)

    output_text, pii_leak_event = pii.scan_output_pii(output_text)
    if pii_leak_event:
        events.append(pii_leak_event)

    policy_event = policy.check_policy(output_text)
    if policy_event:
        events.append(policy_event)
        if policy_event.action_taken == "block":
            return OutputGuardrailResult(
                text=REFUSAL_MESSAGES["policy_violation"],
                refused=True,
                refusal_reason="policy_violation",
                events=events,
            )

    output_text = pii.restore_input_pii(output_text, pii_mapping)
    return OutputGuardrailResult(
        text=output_text, refused=False, refusal_reason=None, events=events
    )
