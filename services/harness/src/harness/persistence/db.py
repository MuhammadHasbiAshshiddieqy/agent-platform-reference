from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from harness.config import settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

engine: AsyncEngine = create_async_engine(settings.database_url, pool_size=10, max_overflow=0)


@asynccontextmanager
async def tenant_session(tenant_id: str) -> AsyncIterator[AsyncConnection]:
    """§7.3 / §23.2k — every tenant-scoped query goes through this.

    Uses `SELECT set_config('app.tenant_id', $1, true)`, not
    `SET LOCAL app.tenant_id = $1` — PostgreSQL's `SET` grammar doesn't
    accept bind parameters at all (confirmed empirically in
    tests/security/test_rls_isolation.py; it's a syntax error with every
    driver). `set_config(..., true)` is the parameter-safe equivalent —
    third arg `true` means "local to this transaction", exactly like
    `SET LOCAL`. One statement, one transaction, `SET LOCAL` semantics.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )
        yield conn


@asynccontextmanager
async def admin_session() -> AsyncIterator[AsyncConnection]:
    """§22.6/§22.8 — `authz.tool_overrides` has no `tenant_id` column and
    no RLS policy (migration 0007): a tool killswitch is a platform-wide
    operational control, not a per-tenant one. Using `tenant_session` here
    would set a tenant context this table doesn't check and can't use."""
    async with engine.begin() as conn:
        yield conn
