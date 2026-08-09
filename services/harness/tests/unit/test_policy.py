"""§9.2 policy violation row: "Definisi policy ... diuji di CI dengan test
case positif & negatif" — this file is that requirement. Part of M4's
DoD 30-case suite (§15 names "policy violation" explicitly).
"""

from __future__ import annotations

from harness.guardrails.policy import check_policy


def test_legal_promise_is_blocked() -> None:
    event = check_policy("Kami menjamin secara hukum bahwa klaim Anda akan disetujui.")
    assert event is not None
    assert event.rule_id == "legal_promise"
    assert event.action_taken == "block"


def test_financial_commitment_is_blocked() -> None:
    event = check_policy("Tenang saja, dijamin cair dalam 3 hari ke depan.")
    assert event is not None
    assert event.rule_id == "financial_commitment"
    assert event.action_taken == "block"


def test_medical_advice_is_blocked() -> None:
    event = check_policy("Berdasarkan gejala Anda, anda didiagnosis dengan flu berat.")
    assert event is not None
    assert event.rule_id == "medical_advice"
    assert event.action_taken == "block"


def test_matching_is_case_insensitive() -> None:
    event = check_policy("KAMI MENJAMIN SECARA HUKUM bahwa ini benar.")
    assert event is not None
    assert event.rule_id == "legal_promise"


def test_ordinary_hr_answer_is_not_flagged() -> None:
    event = check_policy("Sisa cuti Anda 8 hari kerja, berlaku sampai akhir tahun.")
    assert event is None


def test_mentioning_a_policy_word_without_the_full_phrase_is_not_flagged() -> None:
    # "hukum"/"pajak"/"dokter" alone shouldn't trip a rule keyed to a
    # specific committing phrase — only the full phrases in the YAML do.
    event = check_policy("Pertanyaan ini sebaiknya dikonsultasikan ke bagian hukum perusahaan.")
    assert event is None
