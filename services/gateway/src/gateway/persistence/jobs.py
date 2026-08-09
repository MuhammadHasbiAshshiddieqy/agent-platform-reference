"""§8.5 `jobs.async_jobs` (migration 0004) — written by gateway on submit
and read back on poll; async-worker owns every subsequent status
transition (§27.1's "jobs schema dimiliki agent-gateway + async-worker
bersama", the one explicit multi-owner exception to boundary #2).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def insert_async_job(
    conn: AsyncConnection,
    *,
    job_id: str,
    tenant_id: str,
    user_id: str,
    trace_id: str,
    priority: str,
    payload: dict[str, Any],
    callback_url: str | None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO jobs.async_jobs "
            "(id, tenant_id, user_id, trace_id, priority, payload, status, callback_url) "
            "VALUES (:id, :tenant_id, :user_id, :trace_id, :priority, "
            " CAST(:payload AS jsonb), 'queued', :callback_url)"
        ),
        {
            "id": job_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "trace_id": trace_id,
            "priority": priority,
            "payload": json.dumps(payload),
            "callback_url": callback_url,
        },
    )


async def get_async_job(
    conn: AsyncConnection, *, tenant_id: str, job_id: str
) -> dict[str, Any] | None:
    result = await conn.execute(
        text(
            "SELECT id, status, attempts, result, error, created_at, completed_at "
            "FROM jobs.async_jobs WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": tenant_id, "id": job_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None
