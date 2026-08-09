from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from retrieval.config import settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

engine: AsyncEngine = create_async_engine(settings.database_url, pool_size=10, max_overflow=0)


@asynccontextmanager
async def tenant_session(tenant_id: str) -> AsyncIterator[AsyncConnection]:
    """§7.3 / §23.2k. See services/harness/src/harness/persistence/db.py
    for why this uses `set_config(..., true)` and not `SET LOCAL ... = $1`
    (the latter is a Postgres syntax error with every driver).

    §28.9 — `tenant_id` deliberately never appears in the hybrid query's
    own WHERE clause; RLS (via this `set_config`) is the only thing
    enforcing it. A query that forgets this returns zero rows, not
    another tenant's chunks.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )
        yield conn
