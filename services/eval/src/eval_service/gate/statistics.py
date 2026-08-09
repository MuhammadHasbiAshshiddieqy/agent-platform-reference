"""§13.5's statistical machinery — pure math, no I/O, no LLM calls. Kept
separate from `verdict.py`'s rule-combining logic so both halves are
independently unit-testable: this module proves the CI math itself is
correct and reproducible; `verdict.py` proves the pass/fail *rules* are
applied correctly given some CI result.
"""

from __future__ import annotations

import numpy as np

# Fixed, not derived per-run: §15's DoD requires "menjalankan eval dua
# kali berturut pada commit yang sama menghasilkan verdict identik" — a
# per-run-random seed would make the bootstrap resample (and therefore
# the CI bounds near a threshold) different every invocation even for
# byte-identical input deltas. A shared constant, not a "cute" number —
# same convention as the judge's own `seed=42` (§13.4).
BOOTSTRAP_SEED = 42


def median_per_item(scores_by_item: dict[str, list[float]]) -> dict[str, float]:
    """`scores_by_item[item_id]` is the k raw judge scores for that item
    (k=1 for smoke, k=3 for full/nightly, §13.6) — §13.5's "ulangi k=3
    per item, ambil median per item" step."""
    return {item_id: float(np.median(scores)) for item_id, scores in scores_by_item.items()}


def paired_deltas(candidate: dict[str, float], baseline: dict[str, float]) -> list[float]:
    """§13.5: "perbandingan berpasangan per item" — only items present in
    BOTH runs contribute a delta; an item added/removed since the
    baseline was cut has no pair to compare against."""
    return [candidate[item_id] - baseline[item_id] for item_id in candidate if item_id in baseline]


def bootstrap_ci(
    deltas: list[float], *, n: int = 2000, level: float = 0.95, seed: int = BOOTSTRAP_SEED
) -> tuple[float, float]:
    """Percentile bootstrap CI on the mean paired delta. Empty input
    (nothing paired with the baseline) returns `(0.0, 0.0)` — a
    zero-width interval at zero reads as "no evidence of regression",
    the conservative default when there's nothing to compare."""
    if not deltas:
        return (0.0, 0.0)
    rng = np.random.default_rng(seed)
    arr = np.asarray(deltas, dtype=float)
    resample_means = np.empty(n)
    for i in range(n):
        resample = rng.choice(arr, size=arr.shape[0], replace=True)
        resample_means[i] = resample.mean()
    alpha = (1 - level) / 2
    lower = float(np.percentile(resample_means, 100 * alpha))
    upper = float(np.percentile(resample_means, 100 * (1 - alpha)))
    return (lower, upper)


def breaches_absolute_floor(median_score: float, *, target: float, margin: float = 0.10) -> bool:
    """§13.5: "Jika median metrik < target − 0.10, gagal langsung" — a
    catastrophic-regression tripwire that doesn't wait for the (slower,
    statistical) baseline comparison."""
    return median_score < (target - margin)
