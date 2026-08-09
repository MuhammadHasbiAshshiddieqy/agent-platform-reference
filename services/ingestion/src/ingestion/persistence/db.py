from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ingestion.config import settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

engine: AsyncEngine = create_async_engine(settings.database_url, pool_size=5, max_overflow=0)


@asynccontextmanager
async def tenant_session(tenant_id: str) -> AsyncIterator[AsyncConnection]:
    """§7.3 / §23.2k. See services/harness/src/harness/persistence/db.py
    for why this uses `set_config(..., true)` and not `SET LOCAL ... = $1`
    (the latter is a Postgres syntax error with every driver)."""
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )
        yield conn
