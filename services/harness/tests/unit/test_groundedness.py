"""§9.2 groundedness row — advisory-only (`flag`) in this milestone, see
guardrails/groundedness.py's docstring on why `block` waits for a
strict-mode/agent-profile concept that doesn't exist until M5b.
"""

from __future__ import annotations

import httpx
import pytest
from _helpers import mock_model_router, text_response
from harness.guardrails.errors import GuardrailServiceError
from harness.guardrails.groundedness import check_groundedness


@pytest.mark.asyncio
async def test_ungrounded_answer_is_flagged_not_blocked() -> None:
    router = mock_model_router(lambda _: text_response("0.1"))
    event = await check_groundedness(
        "Sisa cuti Anda adalah 100 hari.", ["Kuota cuti tahunan 12 hari kerja."], router
    )
    assert event is not None
    assert event.action_taken == "flag"
    await router.aclose()


@pytest.mark.asyncio
async def test_well_grounded_answer_is_allowed() -> None:
    router = mock_model_router(lambda _: text_response("0.95"))
    event = await check_groundedness(
        "Sisa cuti Anda 8 hari.", ["Kuota cuti tahunan 12 hari kerja."], router
    )
    assert event is not None
    assert event.action_taken == "allow"
    await router.aclose()


@pytest.mark.asyncio
async def test_no_retrieved_chunks_skips_the_check_entirely() -> None:
    router = mock_model_router(lambda _: text_response("0.0"))
    event = await check_groundedness("Halo, ada yang bisa dibantu?", [], router)
    assert event is None
    await router.aclose()


@pytest.mark.asyncio
async def test_unparseable_judge_response_skips_without_erroring() -> None:
    router = mock_model_router(lambda _: text_response("tidak bisa dinilai"))
    event = await check_groundedness("Jawaban.", ["konteks"], router)
    assert event is None
    await router.aclose()


@pytest.mark.asyncio
async def test_model_router_failure_raises_guardrail_service_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    router = mock_model_router(handler)
    with pytest.raises(GuardrailServiceError):
        await check_groundedness("Jawaban.", ["konteks"], router)
    await router.aclose()
