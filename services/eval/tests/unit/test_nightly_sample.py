"""§13.9's nightly-tier report — pure rendering/ranking logic only.
`run_nightly_sample` itself (Langfuse fetch + live judge calls) is
live-verified against the real stack, not unit tested here — same split
`runner.py`'s own tests already draw between deterministic logic (unit)
and the live judge path (manual/live verification).
"""

from __future__ import annotations

import datetime as dt

from eval_service.clients.langfuse import ProductionTrace
from eval_service.nightly_sample import _fmt, _overall, _render_report


def test_fmt_handles_none_and_numbers() -> None:
    assert _fmt(None) == "n/a"
    assert _fmt(0.5) == "0.50"
    assert _fmt(1.0) == "1.00"


def test_overall_is_none_when_nothing_scoreable() -> None:
    assert _overall({"citation_support": None, "faithfulness": None}) is None


def test_overall_averages_only_the_applicable_scores() -> None:
    # context_precision/context_recall are deliberately never in this
    # dict at all (§13.9: no reference for a production trace) — this
    # asserts the averaging logic itself, not that omission.
    scores = {"citation_support": 1.0, "faithfulness": 0.5, "answer_relevancy": None}
    assert _overall(scores) == 0.75


def _trace(trace_id: str, question: str = "q", answer: str = "a") -> ProductionTrace:
    return ProductionTrace(
        trace_id=trace_id,
        question=question,
        answer=answer,
        timestamp=dt.datetime(2026, 8, 11, tzinfo=dt.UTC),
        cited_chunks=[],
    )


def test_render_report_includes_every_ranked_trace_and_never_mentions_the_golden_set() -> None:
    ranked = [
        (
            _trace("trc_1", "why is the sky blue?"),
            {"citation_support": 0.2, "faithfulness": None, "answer_relevancy": None},
            0.2,
        ),
        (
            _trace("trc_2", "another question"),
            {"citation_support": None, "faithfulness": None, "answer_relevancy": None},
            None,
        ),
    ]
    report = _render_report(agent_id="hr-assistant", since=dt.datetime.now(dt.UTC), ranked=ranked)
    assert "trc_1" in report
    assert "trc_2" in report
    assert "why is the sky blue?" in report
    assert "human review only" in report
    # §13.9's own explicit instruction: never implies auto-insertion.
    assert "golden_set.yaml" not in report
    assert "auto-added" in report
