"""§9.1 input size row — deterministic, no model call."""

from __future__ import annotations

from harness.guardrails.size import check_input_size, estimate_tokens


def test_input_within_limit_is_allowed() -> None:
    event = check_input_size("Berapa sisa cuti saya?", max_tokens=1000)
    assert event.action_taken == "allow"


def test_input_over_limit_is_blocked() -> None:
    huge = "x" * 100_000
    event = check_input_size(huge, max_tokens=1000)
    assert event.action_taken == "block"
    assert event.detail is not None
    assert event.detail["estimated_tokens"] > 1000


def test_estimate_tokens_is_at_least_one_for_nonempty_text() -> None:
    assert estimate_tokens("hi") >= 1
