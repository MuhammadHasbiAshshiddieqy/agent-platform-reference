"""§9.1 off-topic row, part of M4's DoD 30-case suite."""

from __future__ import annotations

import httpx
import pytest
from _helpers import mock_model_router, text_response
from harness.guardrails.errors import GuardrailServiceError
from harness.guardrails.offtopic import check_offtopic


@pytest.mark.asyncio
async def test_clearly_off_topic_message_is_blocked() -> None:
    router = mock_model_router(lambda _: text_response("0"))
    event = await check_offtopic(
        "Tolong tuliskan puisi cinta untuk pacar saya.", "hr-assistant", router
    )
    assert event.action_taken == "block"
    await router.aclose()


@pytest.mark.asyncio
async def test_on_topic_message_is_allowed() -> None:
    router = mock_model_router(lambda _: text_response("1"))
    event = await check_offtopic("Berapa sisa cuti saya tahun ini?", "hr-assistant", router)
    assert event.action_taken == "allow"
    await router.aclose()


@pytest.mark.asyncio
async def test_unregistered_agent_id_skips_the_check_without_calling_the_classifier() -> None:
    # No registered description to judge scope against — guessing one
    # (the original design) produced false positives on M1-M3's
    # deliberately domain-agnostic smoke-test prompts. See offtopic.py's
    # module docstring for the concrete failure this replaced.
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return text_response("0")

    router = mock_model_router(handler)
    event = await check_offtopic("Reply with exactly one word: hello.", "m1-smoke-test", router)
    assert event is None
    assert calls == 0
    await router.aclose()


@pytest.mark.asyncio
async def test_unparseable_classifier_response_defaults_to_allow() -> None:
    router = mock_model_router(lambda _: text_response("tidak dapat dinilai"))
    event = await check_offtopic("Pertanyaan apa saja.", "hr-assistant", router)
    assert event.action_taken == "allow"
    await router.aclose()


@pytest.mark.asyncio
async def test_model_router_failure_raises_guardrail_service_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    router = mock_model_router(handler)
    with pytest.raises(GuardrailServiceError):
        await check_offtopic("Pertanyaan apa saja.", "hr-assistant", router)
    await router.aclose()
