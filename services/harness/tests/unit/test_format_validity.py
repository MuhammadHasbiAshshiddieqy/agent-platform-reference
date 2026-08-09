"""§9.2 format validity row."""

from __future__ import annotations

from harness.guardrails.format_validity import format_validity_event, is_valid_format


def test_nonempty_text_is_valid() -> None:
    assert is_valid_format("Sisa cuti Anda 8 hari.") is True


def test_empty_text_is_invalid() -> None:
    assert is_valid_format("") is False


def test_whitespace_only_text_is_invalid() -> None:
    assert is_valid_format("   \n  ") is False


def test_event_reflects_retry_flag() -> None:
    event = format_validity_event(True, retried=True)
    assert event.action_taken == "allow"
    assert event.detail == {"retried": True}

    blocked = format_validity_event(False, retried=True)
    assert blocked.action_taken == "block"
