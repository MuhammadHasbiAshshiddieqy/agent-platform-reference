"""§13.4's Ragas judge wrapper + §13.5's judge-result cache
(`(item_id, sha256(response), judge_model_version, metric)`). Judge
config is verbatim from §13.4: always through model-router, `eval-judge`
alias (never `agent-primary`/`agent-cheap` — ADR-012, "model yang
dievaluasi tidak boleh menjadi jurinya sendiri"), `temperature=0.0`,
`seed=42` so re-scoring identical input is actually deterministic, not
just cached-so-it-looks-deterministic.
"""

from __future__ import annotations

import hashlib
from typing import Any

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr
from ragas.dataset_schema import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness
from sqlalchemy.ext.asyncio import AsyncConnection

from eval_service.config import settings
from eval_service.persistence.repository import get_cached_judge_score, put_cached_judge_score

# §13.9 — bump this manually whenever config/model-router/config.yaml's
# `eval-judge` alias changes to a materially different model; that event
# requires a full re-baseline (§13.4's own text), and this string
# participates in the judge cache key specifically so an old cached
# score can never silently leak into a new judge's results.
JUDGE_MODEL_VERSION = "eval-judge@qwen2.5-3b@v1"

METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]


def response_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class JudgeRunner:
    def __init__(self) -> None:
        judge = LangchainLLMWrapper(
            ChatOpenAI(
                base_url=settings.model_router_url,
                api_key=SecretStr(settings.model_router_key),
                model="eval-judge",
                temperature=0.0,
                seed=42,
            )
        )
        judge_embeddings = LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(
                base_url=settings.model_router_url,
                api_key=SecretStr(settings.model_router_key),
                model="embedding-default",
                # LangChain's `OpenAIEmbeddings` pre-tokenizes input into
                # token-id arrays by default (real OpenAI's embeddings
                # endpoint accepts either form) — Infinity's OpenAI-
                # compatible endpoint only accepts raw strings and
                # rejects the token-id form with a 422 (confirmed live:
                # "Input should be a valid string" on a list[int]
                # payload). `tiktoken_enabled=False` alone just swaps to
                # a *different* tokenizer (HuggingFace `transformers`,
                # not installed — also confirmed live) for the same
                # len-safe-chunking step; `check_embedding_ctx_length=
                # False` skips that step entirely and sends the raw
                # string directly, needing no tokenizer at all — the
                # right fix for short guardrail/RAG-sized inputs that
                # were never at risk of exceeding a context window.
                check_embedding_ctx_length=False,
            )
        )
        self._metrics: dict[str, Any] = {
            "faithfulness": Faithfulness(llm=judge),
            "answer_relevancy": AnswerRelevancy(llm=judge, embeddings=judge_embeddings),
            "context_precision": ContextPrecision(llm=judge),
            "context_recall": ContextRecall(llm=judge),
        }

    async def score_item(
        self,
        conn: AsyncConnection,
        *,
        item_id: str,
        question: str,
        answer: str,
        retrieved_contexts: list[str],
        reference: str | None,
    ) -> dict[str, float]:
        resp_hash = response_hash(answer)
        sample = SingleTurnSample(
            user_input=question,
            response=answer,
            # Ragas rejects an empty list outright for context-dependent
            # metrics — a single empty string is a real "no context was
            # retrieved" signal, not a workaround for a library quirk.
            retrieved_contexts=retrieved_contexts or [""],
            reference=reference or "",
        )
        scores: dict[str, float] = {}
        for name, metric in self._metrics.items():
            cached = await get_cached_judge_score(
                conn,
                item_id=item_id,
                response_hash=resp_hash,
                judge_model_version=JUDGE_MODEL_VERSION,
                metric=name,
            )
            if cached is not None:
                scores[name] = cached
                continue
            score = float(await metric.single_turn_ascore(sample))
            await put_cached_judge_score(
                conn,
                item_id=item_id,
                response_hash=resp_hash,
                judge_model_version=JUDGE_MODEL_VERSION,
                metric=name,
                score=score,
            )
            scores[name] = score
        return scores
