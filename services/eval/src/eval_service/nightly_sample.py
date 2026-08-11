"""`python -m eval_service.nightly_sample --agent-id hr-assistant`
(§13.6/§13.9's nightly-tier production sampling — the "Full + 200 sampel
trace produksi + citation_support" part `run.py --tier nightly` does not
actually implement, since `--tier nightly` today is just `full` run
again, see CLAUDE.md's M8 status for why that scope was cut).

Pulls real traces out of Langfuse (never ClickHouse directly — see
`clients/langfuse.py`'s docstring), scores each with `citation_support`
plus the two Ragas metrics that are valid without a ground-truth
reference (`faithfulness`, `answer_relevancy` — `context_precision`/
`context_recall` need a `reference` no production trace has, so they're
not computed here), and writes the lowest-scoring N to a markdown report.

Deliberately does NOT write anything to `eval.items` or
`seed/eval/golden_set.yaml` — §13.9's own text is explicit: "hasil
anotasi menjadi kandidat item baru... melalui review manual — jangan
memasukkannya otomatis, karena dataset yang tumbuh tanpa kurasi akan
menurunkan kualitas gate secara diam-diam." A human reads the report and
decides, by hand, whether any of it is worth adding to the version-
controlled golden set.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging

from eval_service.clients.langfuse import LangfuseClient, ProductionTrace
from eval_service.config import settings
from eval_service.metrics.citation_support import CitationSupportJudge
from eval_service.metrics.judged import JudgeRunner
from eval_service.persistence.db import session

logger = logging.getLogger("eval_service.nightly_sample")


async def _score_trace(
    trace: ProductionTrace,
    *,
    citation_judge: CitationSupportJudge,
    judge_runner: JudgeRunner,
) -> dict[str, float | None]:
    """Each judge call is wrapped independently — same resilience stance
    `runner.py`'s `run_item` already takes for the golden-set path (see
    its own comment: "a degraded judge must not sink the run"). A batch
    of up to `--limit` production traces run back-to-back is *more*
    exposed to one transient judge failure than a single golden-set item
    is, not less, so skipping this here would be a regression from the
    golden-set path's own standard, not a simplification."""
    citation_support: float | None = None
    try:
        async with session() as conn:
            citation_support = await citation_judge.score(
                conn,
                trace_id=trace.trace_id,
                answer=trace.answer,
                cited_chunks=trace.cited_chunks,
            )
    except Exception as exc:  # noqa: BLE001 — see docstring above
        logger.warning("citation_support failed for trace=%s: %s", trace.trace_id, exc)

    ragas_scores: dict[str, float] = {}
    try:
        # `reference=None` — no ground truth for an organic production
        # trace. `score_item` still computes all four Ragas metrics
        # unconditionally; context_precision/context_recall are
        # meaningless without a reference, so their values are dropped
        # here rather than reported as if they meant something.
        async with session() as conn:
            ragas_scores = await judge_runner.score_item(
                conn,
                item_id=trace.trace_id,
                question=trace.question,
                answer=trace.answer,
                retrieved_contexts=[c.content for c in trace.cited_chunks if c.content],
                reference=None,
            )
    except Exception as exc:  # noqa: BLE001 — see docstring above
        logger.warning("ragas judge failed for trace=%s: %s", trace.trace_id, exc)

    return {
        "citation_support": citation_support,
        "faithfulness": ragas_scores.get("faithfulness"),
        "answer_relevancy": ragas_scores.get("answer_relevancy"),
    }


def _overall(scores: dict[str, float | None]) -> float | None:
    applicable = [v for v in scores.values() if v is not None]
    if not applicable:
        return None
    return sum(applicable) / len(applicable)


type ScoredTrace = tuple[ProductionTrace, dict[str, float | None], float | None]


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _render_report(*, agent_id: str, since: dt.datetime, ranked: list[ScoredTrace]) -> str:
    lines = [
        f"# Production trace review — {agent_id}",
        "",
        f"Sampled since {since.isoformat()}. Lowest-scoring {len(ranked)} traces below, for "
        "**human review only** — §13.9: nothing here is auto-added to the golden set.",
        "",
        "| trace_id | overall | citation_support | faithfulness | answer_relevancy | question |",
        "|---|---|---|---|---|---|",
    ]
    for trace, scores, overall in ranked:
        question_preview = trace.question.replace("\n", " ")[:80]
        lines.append(
            f"| `{trace.trace_id}` | {_fmt(overall)} | {_fmt(scores['citation_support'])} | "
            f"{_fmt(scores['faithfulness'])} | {_fmt(scores['answer_relevancy'])} | "
            f"{question_preview} |"
        )
    lines.append("")
    lines.append("## Full text, lowest-scoring first")
    for trace, _scores, overall in ranked:
        lines.append(f"\n### `{trace.trace_id}` (overall {_fmt(overall)})")
        lines.append(f"\n**Question:**\n\n{trace.question}\n")
        lines.append(f"**Answer:**\n\n{trace.answer}\n")
        if trace.cited_chunks:
            lines.append("**Cited chunks:**\n")
            for chunk in trace.cited_chunks:
                lines.append(f"- `{chunk.chunk_id}` ({chunk.source_uri})")
        else:
            lines.append("**Cited chunks:** none")
    return "\n".join(lines)


async def run_nightly_sample(*, agent_id: str, hours: float, limit: int, surface: int) -> str:
    langfuse = LangfuseClient(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    traces = await langfuse.fetch_recent_traces(agent_id=agent_id, since=since, limit=limit)

    citation_judge = CitationSupportJudge()
    judge_runner = JudgeRunner()

    scored: list[ScoredTrace] = []
    for trace in traces:
        scores = await _score_trace(trace, citation_judge=citation_judge, judge_runner=judge_runner)
        scored.append((trace, scores, _overall(scores)))

    # Traces with nothing scoreable (overall=None) sort last, not first —
    # "we couldn't judge this" is not the same signal as "this scored
    # badly," and shouldn't crowd out genuinely low-scoring traces from
    # the surfaced N.
    scored.sort(key=lambda row: (row[2] is None, row[2] if row[2] is not None else 0.0))
    ranked = scored[:surface]

    report = _render_report(agent_id=agent_id, since=since, ranked=ranked)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
    out_path = settings.reports_dir / f"production_review_{agent_id}_{stamp}.md"
    out_path.write_text(report)
    return str(out_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="§13.9 — sample recent production traces from Langfuse, score with "
        "citation_support/faithfulness/answer_relevancy, surface the lowest-scoring N "
        "for human review. Never writes to the golden set."
    )
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--hours", type=float, default=24.0, help="lookback window")
    parser.add_argument("--limit", type=int, default=200, help="max traces fetched from Langfuse")
    parser.add_argument("--surface", type=int, default=10, help="lowest-scoring N to report")
    args = parser.parse_args()

    out_path = asyncio.run(
        run_nightly_sample(
            agent_id=args.agent_id, hours=args.hours, limit=args.limit, surface=args.surface
        )
    )
    print(f"report written to {out_path}")


if __name__ == "__main__":
    main()
