"""Orchestrates one eval run: sync dataset -> select items (§13.6's tier
rules) -> execute concurrently against the real gateway (§13.7) -> score
-> persist one `eval.runs` row. Deciding pass/fail from the persisted
scores is `gate.py`'s job, a deliberately separate step (§13.5's own
architecture: re-gating shouldn't require re-running the LLM).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection

from eval_service.clients.gateway import GatewayClient, GatewayError
from eval_service.clients.mock_idp import MockIdpClient
from eval_service.config import settings
from eval_service.datasets.loader import sync_golden_set
from eval_service.datasets.sampler import stratified_sample
from eval_service.gate.statistics import median_per_item
from eval_service.gate.verdict import DETERMINISTIC_SPECS, compute_verdict
from eval_service.metrics import deterministic as det
from eval_service.metrics.judged import METRIC_NAMES as RAGAS_METRIC_NAMES
from eval_service.metrics.judged import JudgeRunner
from eval_service.persistence.db import session
from eval_service.persistence.repository import get_baseline_run, get_items, insert_run

logger = logging.getLogger("eval_service.runner")

TIER_DEFAULT_K = {"smoke": 1, "full": 3, "nightly": 3}


@dataclass
class ItemResult:
    item_id: str
    tags: list[str] = field(default_factory=list)
    error: str | None = None
    deterministic: dict[str, float] = field(default_factory=dict)
    ragas_samples: dict[str, list[float]] = field(default_factory=dict)
    # A judge failure (bad structured output, judge model unavailable,
    # ...) never crashes the run — §13's own priority order ("sebisa
    # mungkin ukur secara deterministik... juri hanya untuk hal yang
    # benar-benar tidak bisa diukur dengan cara lain") means a run whose
    # judge is degraded should still produce a real deterministic-metric
    # verdict, not no verdict at all. Recorded per-item so the report
    # can surface *which* items lost judge coverage, distinct from a
    # `.error` (gateway/impersonation failure), which invalidates the
    # deterministic scores too.
    judge_errors: list[str] = field(default_factory=list)


async def run_item(
    item: dict[str, Any],
    *,
    agent_id: str,
    mock_idp: MockIdpClient,
    gateway: GatewayClient,
    judge: JudgeRunner,
    k: int,
    conn: AsyncConnection,
    semaphore: asyncio.Semaphore,
) -> ItemResult:
    async with semaphore:
        result = ItemResult(item_id=item["id"], tags=list(item.get("tags") or []))

        try:
            token = await mock_idp.impersonate(item["actor_user_id"])
        except httpx.HTTPError as exc:
            result.error = f"impersonation failed: {exc}"
            return result

        responses = []
        for _ in range(max(k, 1)):
            try:
                response = await gateway.invoke(
                    token=token, agent_id=agent_id, question=item["question"]
                )
            except GatewayError as exc:
                result.error = f"gateway call failed: HTTP {exc.status_code}: {exc.detail}"
                return result
            if response.eval is None:
                result.error = "no _eval bundle on response — eval mode was not honored"
                return result
            responses.append(response)

        primary = responses[0]
        bundle = primary.eval
        assert bundle is not None
        result.deterministic["tool_selection_accuracy"] = det.tool_selection_accuracy(item, bundle)
        result.deterministic["mutation_safety"] = det.mutation_safety(item, bundle)
        result.deterministic["refusal_appropriateness"] = det.refusal_appropriateness(item, bundle)
        citation_score = det.citation_validity(item, bundle, primary.output.citations)
        if citation_score is not None:
            result.deterministic["citation_validity"] = citation_score
        result.deterministic["capability_leak"] = float(
            det.capability_leak(item, bundle, primary.output.content)
        )
        result.deterministic["pii_leakage"] = float(det.pii_leakage(item, primary.output.content))

        for response in responses:
            b = response.eval
            assert b is not None
            retrieved_contexts = [
                b.retrieved_chunk_contents[cid]
                for cid in b.chunks_in_prompt
                if cid in b.retrieved_chunk_contents
            ]
            try:
                scores = await judge.score_item(
                    conn,
                    item_id=item["id"],
                    question=item["question"],
                    answer=response.output.content,
                    retrieved_contexts=retrieved_contexts,
                    reference=item.get("ground_truth"),
                )
            except Exception as exc:  # noqa: BLE001 — a degraded judge must not sink the run
                logger.warning("judge scoring failed for item=%s: %s", item["id"], exc)
                result.judge_errors.append(str(exc))
                continue
            for name, score in scores.items():
                result.ragas_samples.setdefault(name, []).append(score)

        return result


def aggregate_deterministic(results: list[ItemResult]) -> dict[str, float]:
    aggregate: dict[str, float] = {}
    for name, (kind, _threshold) in DETERMINISTIC_SPECS.items():
        # An item that errored out (gateway/impersonation failure) counts
        # as a failure for the mean-based metrics, never silently drops
        # out of the sample — a systematic outage must tank the gate,
        # not vanish from it.
        values = [
            r.deterministic[name]
            if r.error is None and name in r.deterministic
            else (0.0 if kind == "mean_threshold" and r.error is not None else None)
            for r in results
        ]
        clean_values = [v for v in values if v is not None]
        if kind == "mean_threshold":
            aggregate[name] = sum(clean_values) / len(clean_values) if clean_values else 1.0
        else:  # count_zero
            aggregate[name] = sum(clean_values) if clean_values else 0.0
    return aggregate


def _ragas_medians(results: list[ItemResult]) -> dict[str, dict[str, float]]:
    medians: dict[str, dict[str, float]] = {}
    for metric_name in RAGAS_METRIC_NAMES:
        per_item = {
            r.item_id: r.ragas_samples[metric_name]
            for r in results
            if r.error is None and metric_name in r.ragas_samples
        }
        if per_item:
            medians[metric_name] = median_per_item(per_item)
    return medians


def _extract_ragas_medians_from_run(
    run: dict[str, Any] | None,
) -> dict[str, dict[str, float]] | None:
    if run is None:
        return None
    scores = run["scores"]
    ragas_medians = scores.get("ragas_medians")
    return ragas_medians if ragas_medians else None


async def run_eval(
    *,
    tier: str,
    k: int | None = None,
    git_sha: str = "unknown",
    agent_version: str = "harness@0.1.0",
    prompt_version: str = "system_prompt@v1",
    model_alias: str = "agent-primary",
) -> dict[str, Any]:
    resolved_k = k if k is not None else TIER_DEFAULT_K.get(tier, 1)

    async with session() as conn:
        dataset_id, agent_id, _count = await sync_golden_set(conn, settings.golden_set_path)
        items = await get_items(conn, dataset_id=dataset_id)

    if tier == "smoke":
        items = stratified_sample(
            items, per_tag_quota=settings.smoke_per_tag_quota, total_size=settings.smoke_sample_size
        )

    mock_idp = MockIdpClient(settings.mock_idp_url, settings.mock_idp_timeout_seconds)
    gateway = GatewayClient(
        settings.gateway_url,
        timeout_seconds=settings.gateway_timeout_seconds,
        max_retries=settings.max_retries,
        retry_backoff_seconds=settings.retry_backoff_seconds,
    )
    judge = JudgeRunner()
    semaphore = asyncio.Semaphore(settings.concurrency)

    try:
        async with session() as conn:
            results = await asyncio.gather(
                *[
                    run_item(
                        item,
                        agent_id=agent_id,
                        mock_idp=mock_idp,
                        gateway=gateway,
                        judge=judge,
                        k=resolved_k,
                        conn=conn,
                        semaphore=semaphore,
                    )
                    for item in items
                ]
            )
    finally:
        await mock_idp.aclose()
        await gateway.aclose()

    deterministic_scores = aggregate_deterministic(list(results))
    ragas_medians = _ragas_medians(list(results))

    async with session() as conn:
        baseline_run = await get_baseline_run(conn, dataset_id=dataset_id, agent_id=agent_id)
    baseline_medians = _extract_ragas_medians_from_run(baseline_run)

    verdict = compute_verdict(
        deterministic_scores=deterministic_scores,
        ragas_candidate_medians=ragas_medians,
        ragas_baseline_medians=baseline_medians,
    )

    run_id = f"evr_{uuid.uuid4().hex[:20]}"
    scores_payload: dict[str, Any] = {
        "tier": tier,
        "k": resolved_k,
        "deterministic": deterministic_scores,
        "ragas_medians": ragas_medians,
        "per_item": {
            r.item_id: {
                "tags": r.tags,
                "deterministic": r.deterministic,
                "ragas": r.ragas_samples,
                "error": r.error,
                "judge_errors": r.judge_errors,
            }
            for r in results
        },
        "verdict": {
            "passed": verdict.passed,
            "metrics": [asdict(m) for m in verdict.metrics],
        },
    }

    async with session() as conn:
        await insert_run(
            conn,
            run_id=run_id,
            dataset_id=dataset_id,
            agent_id=agent_id,
            agent_version=agent_version,
            prompt_version=prompt_version,
            model_alias=model_alias,
            git_sha=git_sha,
            dataset_version=dataset_id,
            scores=scores_payload,
            passed=verdict.passed,
        )

    return {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "agent_id": agent_id,
        "verdict": verdict,
        "scores": scores_payload,
    }
