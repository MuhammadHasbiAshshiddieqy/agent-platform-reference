"""`python -m eval_service.report --tier smoke [--run-id ID]
[--comment-pr N]` (§13.8). Renders the persisted run's verdict as
markdown + JSON under `reports/`, and — only when `--comment-pr` is
given AND a `gh` CLI + `GITHUB_TOKEN` are actually available — posts it
as a PR comment. Never fails the build over a missing PR-comment
capability: a report artifact always gets written to disk regardless,
so `actions/upload-artifact` (§13.8's own workflow) always has something
to attach even if the comment step is skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
from typing import Any

from eval_service.config import settings
from eval_service.persistence.db import session
from eval_service.persistence.repository import get_run


def _resolve_run_id(tier: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    path = settings.reports_dir / f"last_run_id_{tier}.txt"
    if not path.exists():
        raise SystemExit(f"no run_id recorded for tier={tier} — run `eval_service.run` first")
    return path.read_text().strip()


def render_markdown(run: dict[str, Any]) -> str:
    scores = run["scores"]
    verdict = scores["verdict"]
    lines = [
        f"## eval report — `{run['id']}`",
        "",
        f"- dataset: `{run['dataset_id']}` (version `{run['dataset_version']}`)",
        f"- agent: `{run['agent_id']}` @ `{run['agent_version']}` "
        f"(prompt `{run['prompt_version']}`)",
        f"- git sha: `{run['git_sha']}`",
        f"- tier: `{scores['tier']}`, k={scores['k']}",
        f"- **verdict: {'✅ PASS' if verdict['passed'] else '❌ BLOCKED'}**",
        "",
        "| metric | status | value | detail |",
        "|---|---|---|---|",
    ]
    for metric in verdict["metrics"]:
        status = (
            "✅"
            if metric["passed"]
            else ("🚫 no override" if not metric["overridable"] else "⚠️ overridable")
        )
        lines.append(
            f"| {metric['name']} | {status} | {metric['value']:.4f} | {metric['detail']} |"
        )

    failing_items = [
        (item_id, detail) for item_id, detail in scores["per_item"].items() if detail.get("error")
    ]
    if failing_items:
        lines += ["", "### items with a run error", ""]
        for item_id, detail in failing_items:
            lines.append(f"- `{item_id}`: {detail['error']}")

    return "\n".join(lines)


def _post_pr_comment(pr_number: str, body: str) -> bool:
    if shutil.which("gh") is None or not os.environ.get("GITHUB_TOKEN"):
        print("skipping PR comment: `gh` CLI or GITHUB_TOKEN not available")
        return False
    subprocess.run(["gh", "pr", "comment", pr_number, "--body", body], check=True)
    return True


async def _main(tier: str, run_id: str | None, comment_pr: str | None) -> None:
    resolved_run_id = _resolve_run_id(tier, run_id)
    async with session() as conn:
        run = await get_run(conn, run_id=resolved_run_id)
    if run is None:
        raise SystemExit(f"no such run: {resolved_run_id}")

    markdown = render_markdown(run)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    (settings.reports_dir / f"{resolved_run_id}.md").write_text(markdown)
    (settings.reports_dir / f"{resolved_run_id}.json").write_text(
        json.dumps(run["scores"], indent=2, default=str)
    )
    print(markdown)

    if comment_pr:
        _post_pr_comment(comment_pr, markdown)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render + optionally post an eval run report (§13.8)"
    )
    parser.add_argument("--tier", choices=["smoke", "full", "nightly"], required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--comment-pr", default=None, help="PR number to comment on, if possible")
    args = parser.parse_args()
    asyncio.run(_main(args.tier, args.run_id, args.comment_pr))


if __name__ == "__main__":
    main()
