"""§8.5 `jobs.async_jobs` — async-worker's half (gateway's insert lives in
`services/gateway/src/gateway/persistence/jobs.py`; §27.1's declared
multi-owner exception to boundary #2, not a duplication bug).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def try_claim_job(conn: AsyncConnection, *, tenant_id: str, job_id: str) -> bool:
    """§23.2f's exact pattern: RabbitMQ is at-least-once, so a worker that
    dies after processing but before ack causes redelivery. `UPDATE ...
    WHERE status IN ('queued','failed')` then check rowcount — zero means
    another delivery of this same message already claimed it; ack and
    walk away rather than double-processing."""
    result = await conn.execute(
        text(
            "UPDATE jobs.async_jobs "
            "SET status = 'running', started_at = now(), attempts = attempts + 1 "
            "WHERE tenant_id = :tenant_id AND id = :id AND status IN ('queued', 'failed')"
        ),
        {"tenant_id": tenant_id, "id": job_id},
    )
    return result.rowcount == 1


async def mark_job_succeeded(
    conn: AsyncConnection, *, tenant_id: str, job_id: str, result: dict[str, Any]
) -> None:
    await conn.execute(
        text(
            "UPDATE jobs.async_jobs "
            "SET status = 'succeeded', result = CAST(:result AS jsonb), completed_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": tenant_id, "id": job_id, "result": json.dumps(result)},
    )


async def mark_job_failed_will_retry(
    conn: AsyncConnection, *, tenant_id: str, job_id: str, error: dict[str, Any]
) -> None:
    """Not terminal — `try_claim_job`'s WHERE clause explicitly allows
    re-claiming a `failed` job, so a later redelivery/retry can pick it
    back up. `error` records what went wrong on *this* attempt."""
    await conn.execute(
        text(
            "UPDATE jobs.async_jobs SET status = 'failed', error = CAST(:error AS jsonb) "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": tenant_id, "id": job_id, "error": json.dumps(error)},
    )


async def mark_job_dead_lettered(
    conn: AsyncConnection, *, tenant_id: str, job_id: str, error: dict[str, Any]
) -> None:
    await conn.execute(
        text(
            "UPDATE jobs.async_jobs "
            "SET status = 'dead_lettered', error = CAST(:error AS jsonb), completed_at = now() "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": tenant_id, "id": job_id, "error": json.dumps(error)},
    )


async def update_job_callback_status(
    conn: AsyncConnection, *, tenant_id: str, job_id: str, callback_status: str
) -> None:
    await conn.execute(
        text(
            "UPDATE jobs.async_jobs SET callback_status = :callback_status "
            "WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": tenant_id, "id": job_id, "callback_status": callback_status},
    )
