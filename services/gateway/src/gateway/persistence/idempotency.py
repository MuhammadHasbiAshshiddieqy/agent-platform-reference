"""§23.2b — race-safe idempotency. INSERT first, never SELECT-then-INSERT:
two requests with the same key arriving at different gateway instances at
the same instant must not both be processed. The unique constraint on
`(tenant_id, idempotency_key)` (§8.5 jobs.idempotency_keys) is what
actually decides the winner; this module just interprets the result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass
class ExistingRecord:
    request_hash: str
    response_body: dict[str, Any] | None
    status_code: int | None


async def try_begin(
    conn: AsyncConnection, *, tenant_id: str, idempotency_key: str, request_hash: str
) -> bool:
    """Returns True if this call won the race (row inserted), False if a
    row already existed (won by someone else, or a genuine replay)."""
    result = await conn.execute(
        text(
            "INSERT INTO jobs.idempotency_keys (tenant_id, idempotency_key, request_hash) "
            "VALUES (:tenant_id, :idempotency_key, :request_hash) "
            "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING "
            "RETURNING tenant_id"
        ),
        {"tenant_id": tenant_id, "idempotency_key": idempotency_key, "request_hash": request_hash},
    )
    return result.first() is not None


async def get_existing(
    conn: AsyncConnection, *, tenant_id: str, idempotency_key: str
) -> ExistingRecord | None:
    result = await conn.execute(
        text(
            "SELECT request_hash, response_body, status_code FROM jobs.idempotency_keys "
            "WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key"
        ),
        {"tenant_id": tenant_id, "idempotency_key": idempotency_key},
    )
    row = result.first()
    if row is None:
        return None
    return ExistingRecord(request_hash=row[0], response_body=row[1], status_code=row[2])


async def complete(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    idempotency_key: str,
    response_body: dict[str, Any],
    status_code: int,
) -> None:
    await conn.execute(
        text(
            "UPDATE jobs.idempotency_keys "
            "SET response_body = CAST(:response_body AS jsonb), status_code = :status_code "
            "WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key"
        ),
        {
            "tenant_id": tenant_id,
            "idempotency_key": idempotency_key,
            "response_body": json.dumps(response_body),
            "status_code": status_code,
        },
    )
