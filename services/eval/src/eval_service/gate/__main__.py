"""`python -m eval_service.gate --tier smoke [--run-id ID] [--promote-
baseline]` (§13.8). Pure re-derivation from an already-persisted
`eval.runs.scores` — never calls the LLM again, so gating (unlike
`run.py`) is cheap and, per §15's DoD, must give an IDENTICAL verdict
every time it's invoked against the same run_id: `gate/statistics.py`'s
fixed bootstrap seed is what makes that true even though the gate math
involves a resampling procedure.

Exit code is the CI signal: 0 = pass, 1 = blocked (§13.8's verdict
table) — whether or not a human could still override it is a PR-review-
process decision, not something this exit code encodes.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from eval_service.config import settings
from eval_service.gate.verdict import Verdict, compute_verdict
from eval_service.persistence.db import session
from eval_service.persistence.repository import get_baseline_run, get_run, promote_baseline


def _resolve_run_id(tier: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    path = settings.reports_dir / f"last_run_id_{tier}.txt"
    if not path.exists():
        raise SystemExit(
            f"no run_id recorded for tier={tier} ({path} missing) — run `eval_service.run` first"
        )
    return path.read_text().strip()


async def _main(tier: str, run_id: str | None, promote: bool, changed_by: str) -> Verdict:
    resolved_run_id = _resolve_run_id(tier, run_id)

    async with session() as conn:
        run = await get_run(conn, run_id=resolved_run_id)
        if run is None:
            raise SystemExit(f"no such run: {resolved_run_id}")

        baseline_run = await get_baseline_run(
            conn, dataset_id=run["dataset_id"], agent_id=run["agent_id"]
        )
        baseline_medians = (
            baseline_run["scores"].get("ragas_medians") if baseline_run is not None else None
        )

        verdict = compute_verdict(
            deterministic_scores=run["scores"]["deterministic"],
            ragas_candidate_medians=run["scores"].get("ragas_medians"),
            ragas_baseline_medians=baseline_medians or None,
        )

        verdict_label = "PASS" if verdict.passed else "BLOCKED"
        print(f"gate: run_id={resolved_run_id} tier={tier} verdict={verdict_label}")
        for metric in verdict.metrics:
            status = "PASS" if metric.passed else "FAIL"
            tag = " [NO OVERRIDE]" if (not metric.passed and not metric.overridable) else ""
            print(f"  [{status}] {metric.name} = {metric.value:.4f} ({metric.detail}){tag}")

        # §13.8: "Semua lulus -> Lolos; baseline diperbarui otomatis
        # setelah merge ke main" — `--promote-baseline` is what a
        # post-merge CI step passes, never the PR-time smoke/full jobs.
        if promote and verdict.passed:
            await promote_baseline(
                conn,
                baseline_change_id=f"blc_{uuid.uuid4().hex[:20]}",
                dataset_id=run["dataset_id"],
                agent_id=run["agent_id"],
                from_run_id=baseline_run["id"] if baseline_run is not None else None,
                to_run_id=resolved_run_id,
                reason="CI: all gates passed on merge to main",
                changed_by=changed_by,
                auto=True,
            )
            print(f"baseline promoted: {resolved_run_id}")

    return verdict


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate an already-persisted eval run (§13.8)")
    parser.add_argument("--tier", choices=["smoke", "full", "nightly"], required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--promote-baseline", action="store_true")
    parser.add_argument("--changed-by", default="ci")
    args = parser.parse_args()
    verdict = asyncio.run(_main(args.tier, args.run_id, args.promote_baseline, args.changed_by))
    raise SystemExit(0 if verdict.passed else 1)


if __name__ == "__main__":
    main()
