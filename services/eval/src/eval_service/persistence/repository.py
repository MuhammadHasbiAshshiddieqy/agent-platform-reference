"""§13.2/§13.9/§13.5 — raw SQL via SQLAlchemy Core `text()`, consistent
with migrations/ (no ORM) and every other service in this repo.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def upsert_dataset(
    conn: AsyncConnection, *, dataset_id: str, name: str, agent_id: str, description: str | None
) -> None:
    await conn.execute(
        text(
            "INSERT INTO eval.datasets (id, name, agent_id, description) "
            "VALUES (:id, :name, :agent_id, :description) "
            "ON CONFLICT (id) DO UPDATE SET "
            "name = EXCLUDED.name, agent_id = EXCLUDED.agent_id, description = EXCLUDED.description"
        ),
        {"id": dataset_id, "name": name, "agent_id": agent_id, "description": description},
    )


async def upsert_item(conn: AsyncConnection, *, item: dict[str, Any]) -> None:
    await conn.execute(
        text(
            "INSERT INTO eval.items "
            "(id, dataset_id, question, ground_truth, contexts, actor_user_id, actor_role, "
            " actor_acl_group_ids, expected_required_tools, forbidden_tools, allowed_mutations, "
            " should_refuse, forbidden_output_terms, allowed_pii, expects_citation, tags, "
            " difficulty, source_trace) "
            "VALUES (:id, :dataset_id, :question, :ground_truth, CAST(:contexts AS jsonb), "
            " :actor_user_id, :actor_role, :actor_acl_group_ids, :expected_required_tools, "
            " :forbidden_tools, :allowed_mutations, :should_refuse, :forbidden_output_terms, "
            " :allowed_pii, :expects_citation, :tags, :difficulty, :source_trace) "
            "ON CONFLICT (id) DO UPDATE SET "
            "dataset_id = EXCLUDED.dataset_id, question = EXCLUDED.question, "
            "ground_truth = EXCLUDED.ground_truth, contexts = EXCLUDED.contexts, "
            "actor_user_id = EXCLUDED.actor_user_id, actor_role = EXCLUDED.actor_role, "
            "actor_acl_group_ids = EXCLUDED.actor_acl_group_ids, "
            "expected_required_tools = EXCLUDED.expected_required_tools, "
            "forbidden_tools = EXCLUDED.forbidden_tools, "
            "allowed_mutations = EXCLUDED.allowed_mutations, "
            "should_refuse = EXCLUDED.should_refuse, "
            "forbidden_output_terms = EXCLUDED.forbidden_output_terms, "
            "allowed_pii = EXCLUDED.allowed_pii, expects_citation = EXCLUDED.expects_citation, "
            "tags = EXCLUDED.tags, difficulty = EXCLUDED.difficulty, "
            "source_trace = EXCLUDED.source_trace"
        ),
        {
            "id": item["id"],
            "dataset_id": item["dataset_id"],
            "question": item["question"],
            "ground_truth": item.get("ground_truth"),
            "contexts": json.dumps(item.get("contexts")),
            "actor_user_id": item["actor_user_id"],
            "actor_role": item["actor_role"],
            "actor_acl_group_ids": item.get("actor_acl_group_ids", []),
            "expected_required_tools": item.get("expected_required_tools", []),
            "forbidden_tools": item.get("forbidden_tools", []),
            "allowed_mutations": item.get("allowed_mutations", []),
            "should_refuse": item.get("should_refuse", False),
            "forbidden_output_terms": item.get("forbidden_output_terms", []),
            "allowed_pii": item.get("allowed_pii", []),
            "expects_citation": item.get("expects_citation", True),
            "tags": item.get("tags", []),
            "difficulty": item.get("difficulty"),
            "source_trace": item.get("source_trace"),
        },
    )


async def get_items(
    conn: AsyncConnection, *, dataset_id: str, item_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    if item_ids is not None:
        result = await conn.execute(
            text("SELECT * FROM eval.items WHERE dataset_id = :dataset_id AND id = ANY(:item_ids)"),
            {"dataset_id": dataset_id, "item_ids": item_ids},
        )
    else:
        result = await conn.execute(
            text("SELECT * FROM eval.items WHERE dataset_id = :dataset_id"),
            {"dataset_id": dataset_id},
        )
    return [dict(row) for row in result.mappings().all()]


async def insert_run(
    conn: AsyncConnection,
    *,
    run_id: str,
    dataset_id: str,
    agent_id: str,
    agent_version: str,
    prompt_version: str,
    model_alias: str,
    git_sha: str,
    dataset_version: str,
    scores: dict[str, Any],
    passed: bool,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO eval.runs "
            "(id, dataset_id, agent_id, agent_version, prompt_version, model_alias, git_sha, "
            " dataset_version, scores, passed) "
            "VALUES (:id, :dataset_id, :agent_id, :agent_version, :prompt_version, :model_alias, "
            " :git_sha, :dataset_version, CAST(:scores AS jsonb), :passed)"
        ),
        {
            "id": run_id,
            "dataset_id": dataset_id,
            "agent_id": agent_id,
            "agent_version": agent_version,
            "prompt_version": prompt_version,
            "model_alias": model_alias,
            "git_sha": git_sha,
            "dataset_version": dataset_version,
            "scores": json.dumps(scores),
            "passed": passed,
        },
    )


async def get_run(conn: AsyncConnection, *, run_id: str) -> dict[str, Any] | None:
    result = await conn.execute(text("SELECT * FROM eval.runs WHERE id = :id"), {"id": run_id})
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def get_baseline_run(
    conn: AsyncConnection, *, dataset_id: str, agent_id: str
) -> dict[str, Any] | None:
    result = await conn.execute(
        text(
            "SELECT * FROM eval.runs WHERE dataset_id = :dataset_id AND agent_id = :agent_id "
            "AND is_baseline"
        ),
        {"dataset_id": dataset_id, "agent_id": agent_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def promote_baseline(
    conn: AsyncConnection,
    *,
    baseline_change_id: str,
    dataset_id: str,
    agent_id: str,
    from_run_id: str | None,
    to_run_id: str,
    reason: str,
    changed_by: str,
    auto: bool,
) -> None:
    # §13.9 — the partial unique index (`eval.runs (dataset_id, agent_id)
    # WHERE is_baseline`) means only one row may have `is_baseline = true`
    # at a time; clear the old one first in the same transaction.
    await conn.execute(
        text(
            "UPDATE eval.runs SET is_baseline = false "
            "WHERE dataset_id = :dataset_id AND agent_id = :agent_id AND is_baseline"
        ),
        {"dataset_id": dataset_id, "agent_id": agent_id},
    )
    await conn.execute(
        text("UPDATE eval.runs SET is_baseline = true WHERE id = :id"), {"id": to_run_id}
    )
    await conn.execute(
        text(
            "INSERT INTO eval.baseline_changes "
            "(id, dataset_id, agent_id, from_run_id, to_run_id, reason, changed_by, auto) "
            "VALUES (:id, :dataset_id, :agent_id, :from_run_id, :to_run_id, :reason, "
            " :changed_by, :auto)"
        ),
        {
            "id": baseline_change_id,
            "dataset_id": dataset_id,
            "agent_id": agent_id,
            "from_run_id": from_run_id,
            "to_run_id": to_run_id,
            "reason": reason,
            "changed_by": changed_by,
            "auto": auto,
        },
    )


async def get_cached_judge_score(
    conn: AsyncConnection,
    *,
    item_id: str,
    response_hash: str,
    judge_model_version: str,
    metric: str,
) -> float | None:
    result = await conn.execute(
        text(
            "SELECT score FROM eval.judge_cache WHERE item_id = :item_id "
            "AND response_hash = :response_hash AND judge_model_version = :judge_model_version "
            "AND metric = :metric"
        ),
        {
            "item_id": item_id,
            "response_hash": response_hash,
            "judge_model_version": judge_model_version,
            "metric": metric,
        },
    )
    row = result.first()
    return float(row[0]) if row is not None else None


async def put_cached_judge_score(
    conn: AsyncConnection,
    *,
    item_id: str,
    response_hash: str,
    judge_model_version: str,
    metric: str,
    score: float,
    reason: str | None = None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO eval.judge_cache "
            "(item_id, response_hash, judge_model_version, metric, score, reason) "
            "VALUES (:item_id, :response_hash, :judge_model_version, :metric, :score, :reason) "
            "ON CONFLICT (item_id, response_hash, judge_model_version, metric) "
            "DO UPDATE SET score = EXCLUDED.score, reason = EXCLUDED.reason"
        ),
        {
            "item_id": item_id,
            "response_hash": response_hash,
            "judge_model_version": judge_model_version,
            "metric": metric,
            "score": score,
            "reason": reason,
        },
    )
