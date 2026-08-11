"""§13.4's `citation_support` — a hand-rolled judge (not Ragas), so its
verdict-parsing and per-chunk-averaging logic gets the same direct unit
coverage as `metrics/deterministic.py`'s pure functions, plus a mocked-
LLM path for `CitationSupportJudge.score` itself since that part talks
to a real model in production. Live-verified separately (§13.9's
nightly-tier pipeline, `nightly_sample.py`) against the real `eval-judge`
alias — this file exists so the parsing/averaging logic doesn't depend
on a live model to catch a regression.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from eval_service.clients.langfuse import CitedChunk
from eval_service.metrics.citation_support import CitationSupportJudge, _parse_verdict


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("VERDICT: yes", True),
        ("VERDICT: no", False),
        ("Some reasoning first.\nVERDICT: yes", True),
        ("verdict: YES", True),  # case-insensitive
        ("no clear verdict line here", False),  # fail closed on ambiguity
        ("", False),
    ],
)
def test_parse_verdict(raw: str, expected: bool) -> None:
    assert _parse_verdict(raw) is expected


@pytest.mark.asyncio
async def test_score_returns_none_when_no_chunks_have_content() -> None:
    judge = CitationSupportJudge.__new__(CitationSupportJudge)  # skip __init__'s real LLM client
    score = await judge.score(
        conn=None,  # type: ignore[arg-type]
        trace_id="trc_1",
        answer="some answer",
        cited_chunks=[CitedChunk(chunk_id="c1", source_uri="doc.md", content="")],
    )
    assert score is None


@pytest.mark.asyncio
async def test_score_averages_verdicts_across_chunks() -> None:
    judge = CitationSupportJudge.__new__(CitationSupportJudge)
    judge._llm = AsyncMock()  # type: ignore[attr-defined]
    # Two chunks: one supports the claim, one doesn't -> average 0.5.
    judge._llm.ainvoke.side_effect = [  # type: ignore[attr-defined]
        SimpleNamespace(content="VERDICT: yes"),
        SimpleNamespace(content="VERDICT: no"),
    ]
    chunks = [
        CitedChunk(chunk_id="c1", source_uri="doc.md", content="supports the claim"),
        CitedChunk(chunk_id="c2", source_uri="doc.md", content="unrelated text"),
    ]
    with (
        patch(
            "eval_service.metrics.citation_support.get_cached_judge_score",
            AsyncMock(return_value=None),
        ),
        patch(
            "eval_service.metrics.citation_support.put_cached_judge_score", AsyncMock()
        ) as put_mock,
    ):
        score = await judge.score(
            conn=None,  # type: ignore[arg-type]
            trace_id="trc_1",
            answer="the claim",
            cited_chunks=chunks,
        )
    assert score == 0.5
    assert put_mock.await_count == 2


@pytest.mark.asyncio
async def test_score_uses_cache_and_skips_the_llm_call() -> None:
    judge = CitationSupportJudge.__new__(CitationSupportJudge)
    judge._llm = AsyncMock()  # type: ignore[attr-defined]
    chunks = [CitedChunk(chunk_id="c1", source_uri="doc.md", content="some content")]
    with patch(
        "eval_service.metrics.citation_support.get_cached_judge_score",
        AsyncMock(return_value=1.0),
    ):
        score = await judge.score(
            conn=None,  # type: ignore[arg-type]
            trace_id="trc_1",
            answer="the claim",
            cited_chunks=chunks,
        )
    assert score == 1.0
    judge._llm.ainvoke.assert_not_called()  # type: ignore[attr-defined]
