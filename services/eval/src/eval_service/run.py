"""`python -m eval_service.run --tier smoke --git-sha <sha> [--k N]`
(§13.8's CI workflow). Executes the tier against the live gateway,
computes + persists one `eval.runs` row, and always exits 0 — deciding
pass/fail for CI purposes is `gate.py`'s job, a deliberately separate
step (re-gating an already-persisted run shouldn't require re-running
the LLM). Writes the resulting run_id to `reports/last_run_id_
{tier}.txt` so `gate.py`/`report.py` can find it without a flag.
"""

from __future__ import annotations

import argparse
import asyncio

from eval_service.config import settings
from eval_service.runner import run_eval


async def _main(tier: str, k: int | None, git_sha: str) -> None:
    result = await run_eval(tier=tier, k=k, git_sha=git_sha)
    verdict = result["verdict"]

    print(f"run_id={result['run_id']} dataset_id={result['dataset_id']} passed={verdict.passed}")
    for metric in verdict.metrics:
        status = "PASS" if metric.passed else "FAIL"
        print(f"  [{status}] {metric.name} = {metric.value:.4f} ({metric.detail})")

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    (settings.reports_dir / f"last_run_id_{tier}.txt").write_text(result["run_id"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an eval tier against the live gateway (§13.7)"
    )
    parser.add_argument("--tier", choices=["smoke", "full", "nightly"], required=True)
    parser.add_argument(
        "--k", type=int, default=None, help="overrides the tier's default k (§13.6)"
    )
    parser.add_argument("--git-sha", default="unknown")
    args = parser.parse_args()
    asyncio.run(_main(args.tier, args.k, args.git_sha))


if __name__ == "__main__":
    main()
