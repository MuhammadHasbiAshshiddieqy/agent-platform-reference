"""§9.1/§9.2 PII rows, part of M4's DoD 30-case suite (§15's M4 DoD names
PII explicitly). Pure logic — Presidio + a blank spaCy pipeline run
locally, no model-router call, no DB.
"""

from __future__ import annotations

from harness.guardrails.pii import redact_input_pii, restore_input_pii, scan_output_pii


def test_nik_is_redacted() -> None:
    text = "NIK saya 3271050101990001, mohon dibantu."
    redacted, mapping, event = redact_input_pii(text)
    assert "3271050101990001" not in redacted
    assert "[NIK_1]" in redacted
    assert mapping["[NIK_1]"] == "3271050101990001"
    assert event is not None
    assert event.action_taken == "redact"


def test_npwp_dotted_format_is_redacted_without_context() -> None:
    text = "NPWP perusahaan: 12.345.678.9-012.345 untuk keperluan pajak."
    redacted, mapping, _ = redact_input_pii(text)
    assert "12.345.678.9-012.345" not in redacted
    assert any(v == "12.345.678.9-012.345" for v in mapping.values())


def test_bare_15_digit_number_needs_npwp_context_to_redact() -> None:
    with_context = "NPWP saya 123456789012345 untuk laporan pajak."
    redacted, mapping, _ = redact_input_pii(with_context)
    assert "123456789012345" not in redacted

    without_context = "Nomor pesanan saya 123456789012345 belum sampai."
    redacted2, mapping2, _ = redact_input_pii(without_context)
    assert "123456789012345" in redacted2
    assert mapping2 == {}


def test_bank_account_number_needs_rekening_context_to_redact() -> None:
    with_context = "Nomor rekening saya 1234567890123 di BCA."
    redacted, mapping, _ = redact_input_pii(with_context)
    assert "1234567890123" not in redacted
    assert any(v == "1234567890123" for v in mapping.values())

    without_context = "Nomor antrian saya 1234567890123 hari ini."
    redacted2, mapping2, _ = redact_input_pii(without_context)
    assert "1234567890123" in redacted2
    assert mapping2 == {}


def test_phone_number_is_redacted() -> None:
    text = "Bisa hubungi saya di nomor HP 081234567890."
    redacted, mapping, _ = redact_input_pii(text)
    assert "081234567890" not in redacted
    assert any(v == "081234567890" for v in mapping.values())


def test_clean_text_is_untouched() -> None:
    text = "Berapa sisa cuti saya tahun ini?"
    redacted, mapping, event = redact_input_pii(text)
    assert redacted == text
    assert mapping == {}
    assert event is None


def test_multiple_entities_get_distinct_numbered_placeholders() -> None:
    text = "NIK saya 3271050101990001, HP saya 081234567890."
    redacted, mapping, _ = redact_input_pii(text)
    assert "[NIK_1]" in redacted
    assert "[NO_HP_1]" in redacted
    assert len(mapping) == 2


def test_overlapping_nik_and_rekening_pattern_redacted_only_once() -> None:
    # A 16-digit NIK also matches the 9-16 digit rekening pattern; without
    # de-duplication this would double-redact the same span.
    text = "NIK saya 3271050101990001 untuk verifikasi KTP."
    redacted, mapping, _ = redact_input_pii(text)
    assert redacted.count("[") == 1
    assert len(mapping) == 1


def test_restore_input_pii_reconstructs_the_original_text() -> None:
    text = "NIK saya 3271050101990001, HP saya 081234567890."
    redacted, mapping, _ = redact_input_pii(text)
    restored = restore_input_pii(redacted, mapping)
    assert restored == text


def test_restore_is_a_no_op_when_mapping_is_empty() -> None:
    text = "Halo, apa kabar?"
    assert restore_input_pii(text, {}) == text


def test_scan_output_pii_masks_model_generated_nik_irreversibly() -> None:
    text = "Contoh format NIK: 3299998887776665 untuk ilustrasi."
    redacted, event = scan_output_pii(text)
    assert "3299998887776665" not in redacted
    assert "<ID_NIK>" in redacted
    assert event is not None
    assert event.rule_id == "pii_leakage"
    assert event.action_taken == "redact"


def test_scan_output_pii_leaves_clean_text_untouched() -> None:
    text = "Sisa cuti Anda 8 hari kerja."
    redacted, event = scan_output_pii(text)
    assert redacted == text
    assert event is None
