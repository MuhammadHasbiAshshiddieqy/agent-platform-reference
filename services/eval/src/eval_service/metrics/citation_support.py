"""§13.4's `citation_support` — "apakah chunk tersebut benar-benar
*mendukung* kalimat yang dilekatinya... diukur oleh citation_support
(juri LLM, informasional dulu, baru di-gate setelah punya data baseline
stabil)". Not a Ragas metric (Ragas has nothing under this name) — a
direct, hand-rolled judge call, same `eval-judge` alias / temperature=0 /
seed=42 convention as `metrics/judged.py`, sharing its judge-cache table
but never importing its `JudgeRunner` (that class computes a fixed set of
Ragas metrics per `SingleTurnSample`; this is a different shape of call
entirely).

Deliberately per-citation, not per-answer: one answer citing three
chunks where only one actually supports its claim should score ~0.33,
not pass-or-fail on the answer as a whole.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection

from eval_service.clients.langfuse import CitedChunk
from eval_service.config import settings
from eval_service.metrics.judged import JUDGE_MODEL_VERSION, response_hash
from eval_service.persistence.repository import get_cached_judge_score, put_cached_judge_score

METRIC_NAME = "citation_support"

_PROMPT = """You are checking whether a source passage actually supports a claim.

Claim (from an AI assistant's answer):
{answer}

Source passage (what the claim cites as its basis):
{chunk_content}

Does the source passage support the claim? Reply with exactly one line in this
format, nothing else:
VERDICT: yes|no
"""


def _parse_verdict(raw: str) -> bool:
    for line in raw.splitlines():
        line = line.strip().lower()
        if line.startswith("verdict:"):
            return "yes" in line
    # No parseable verdict is treated as unsupported, not skipped — a
    # judge that can't answer the question isn't evidence the citation
    # is fine, same "fail closed on ambiguity" stance §13.3's
    # zero-tolerance metrics already take.
    return False


class CitationSupportJudge:
    def __init__(self) -> None:
        self._llm = ChatOpenAI(
            base_url=settings.model_router_url,
            api_key=SecretStr(settings.model_router_key),
            model="eval-judge",
            temperature=0.0,
            seed=42,
        )

    async def score(
        self,
        conn: AsyncConnection,
        *,
        trace_id: str,
        answer: str,
        cited_chunks: list[CitedChunk],
    ) -> float | None:
        """`None` when there's nothing to check (no citations at all) —
        same "not applicable" convention `citation_validity`
        (deterministic.py) already uses, not a 0."""
        scoreable = [c for c in cited_chunks if c.content]
        if not scoreable:
            return None

        resp_hash = response_hash(answer)
        verdicts: list[bool] = []
        for chunk in scoreable:
            cache_key = f"{trace_id}:{chunk.chunk_id}"
            cached = await get_cached_judge_score(
                conn,
                item_id=cache_key,
                response_hash=resp_hash,
                judge_model_version=JUDGE_MODEL_VERSION,
                metric=METRIC_NAME,
            )
            if cached is not None:
                verdicts.append(cached >= 0.5)
                continue

            raw = await self._llm.ainvoke(
                _PROMPT.format(answer=answer, chunk_content=chunk.content)
            )
            supported = _parse_verdict(str(raw.content))
            await put_cached_judge_score(
                conn,
                item_id=cache_key,
                response_hash=resp_hash,
                judge_model_version=JUDGE_MODEL_VERSION,
                metric=METRIC_NAME,
                score=1.0 if supported else 0.0,
                reason=str(raw.content)[:500],
            )
            verdicts.append(supported)

        return sum(verdicts) / len(verdicts)
