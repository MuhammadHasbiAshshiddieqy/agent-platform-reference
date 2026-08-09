"""§13.5/§13.8's gate math and rule-combining, pure unit tests — no LLM,
no DB. Two of these are the milestone's literal DoD proofs: `test_
gate_is_deterministic_across_repeated_calls` (§15: "menjalankan eval dua
kali berturut pada commit yang sama menghasilkan verdict identik") and
`test_a_5_percent_regression_is_detected_while_normal_noise_is_not`
(§15: "regresi buatan sebesar 5% ... terdeteksi; derau normal antar run
tidak memicu kegagalan").
"""

from __future__ import annotations

from eval_service.gate.statistics import (
    bootstrap_ci,
    breaches_absolute_floor,
    median_per_item,
    paired_deltas,
)
from eval_service.gate.verdict import compute_verdict, gate_deterministic


def _passing_deterministic_scores() -> dict[str, float]:
    return {
        "tool_selection_accuracy": 1.0,
        "mutation_safety": 1.0,
        "citation_validity": 1.0,
        "refusal_appropriateness": 1.0,
        "capability_leak": 0.0,
        "pii_leakage": 0.0,
    }


def test_gate_deterministic_all_pass() -> None:
    metrics = gate_deterministic(_passing_deterministic_scores())
    assert all(m.passed for m in metrics)


def test_gate_deterministic_zero_tolerance_metric_is_never_overridable() -> None:
    scores = _passing_deterministic_scores()
    scores["mutation_safety"] = 0.99  # a single unauthorized execution
    metrics = {m.name: m for m in gate_deterministic(scores)}
    assert metrics["mutation_safety"].passed is False
    assert metrics["mutation_safety"].overridable is False


def test_gate_deterministic_non_zero_tolerance_failure_is_overridable() -> None:
    scores = _passing_deterministic_scores()
    scores["tool_selection_accuracy"] = 0.80
    metrics = {m.name: m for m in gate_deterministic(scores)}
    assert metrics["tool_selection_accuracy"].passed is False
    assert metrics["tool_selection_accuracy"].overridable is True


def test_capability_leak_count_of_one_fails_even_though_rate_would_round_to_near_zero() -> None:
    # §13.3: "hitungan, bukan rata-rata" — one leak must fail regardless
    # of how many items were in the run (this function only ever sees
    # the already-aggregated count, so the "not diluted" property is
    # actually enforced by `runner.py`'s aggregation, not here — but the
    # gate itself must still treat 1.0 as a hard failure, not "close to
    # zero").
    scores = _passing_deterministic_scores()
    scores["capability_leak"] = 1.0
    metrics = {m.name: m for m in gate_deterministic(scores)}
    assert metrics["capability_leak"].passed is False


def test_verdict_passed_requires_every_metric_to_pass() -> None:
    verdict = compute_verdict(deterministic_scores=_passing_deterministic_scores())
    assert verdict.passed is True

    failing_scores = _passing_deterministic_scores()
    failing_scores["pii_leakage"] = 2.0
    failing_verdict = compute_verdict(deterministic_scores=failing_scores)
    assert failing_verdict.passed is False
    assert len(failing_verdict.blocking_no_override) == 1
    assert failing_verdict.blocking_no_override[0].name == "pii_leakage"


def test_gate_is_deterministic_across_repeated_calls() -> None:
    """§15's DoD, anti-flaky proof: gating the SAME persisted scores
    twice must produce an identical verdict — including the Ragas paired
    -CI branch, whose bootstrap resample is the one place randomness
    could otherwise leak in."""
    deterministic_scores = _passing_deterministic_scores()
    candidate_medians = {
        "faithfulness": {f"it_{i}": 0.85 + (i % 3) * 0.01 for i in range(20)},
    }
    baseline_medians = {
        "faithfulness": {f"it_{i}": 0.90 for i in range(20)},
    }

    verdict_1 = compute_verdict(
        deterministic_scores=deterministic_scores,
        ragas_candidate_medians=candidate_medians,
        ragas_baseline_medians=baseline_medians,
    )
    verdict_2 = compute_verdict(
        deterministic_scores=deterministic_scores,
        ragas_candidate_medians=candidate_medians,
        ragas_baseline_medians=baseline_medians,
    )

    assert verdict_1.passed == verdict_2.passed
    assert [(m.name, m.passed, round(m.value, 10), m.detail) for m in verdict_1.metrics] == [
        (m.name, m.passed, round(m.value, 10), m.detail) for m in verdict_2.metrics
    ]


def test_a_5_percent_regression_is_detected_while_normal_noise_is_not() -> None:
    """§15's DoD, second proof. A uniform -0.05 shift on every paired
    item must fail the CI check; independent +-0.01 noise around zero
    must not."""
    item_ids = [f"it_{i}" for i in range(30)]
    baseline = {item_id: 0.90 for item_id in item_ids}

    regressed_candidate = {item_id: 0.85 for item_id in item_ids}  # uniform -0.05
    regressed_deltas = paired_deltas(regressed_candidate, baseline)
    _lower, upper = bootstrap_ci(regressed_deltas)
    assert upper < -0.02, "a uniform 5% regression must be caught"

    # Small alternating +-0.01 noise around the baseline, mean ~0 —
    # normal run-to-run variance, must NOT trip the gate.
    noisy_candidate = {
        item_id: 0.90 + (0.01 if i % 2 == 0 else -0.01) for i, item_id in enumerate(item_ids)
    }
    noisy_deltas = paired_deltas(noisy_candidate, baseline)
    _lower2, upper2 = bootstrap_ci(noisy_deltas)
    assert upper2 >= -0.02, "ordinary noise must not trigger a false regression failure"


def test_absolute_floor_catches_catastrophic_regression_without_a_baseline() -> None:
    assert breaches_absolute_floor(0.75, target=0.90) is True  # 0.90 - 0.10 = 0.80 floor
    assert breaches_absolute_floor(0.85, target=0.90) is False


def test_median_per_item_uses_median_not_mean_so_one_outlier_is_resisted() -> None:
    # k=3 samples, one wild outlier — median should land on the middle
    # value, not get dragged by the outlier the way a mean would.
    medians = median_per_item({"it_1": [0.9, 0.88, 0.1]})
    assert medians["it_1"] == 0.88


def test_paired_deltas_only_pairs_items_present_in_both_runs() -> None:
    candidate = {"it_1": 0.9, "it_2": 0.8, "it_new": 0.5}
    baseline = {"it_1": 0.85, "it_2": 0.75}
    deltas = paired_deltas(candidate, baseline)
    assert len(deltas) == 2  # it_new has no baseline pair, excluded
