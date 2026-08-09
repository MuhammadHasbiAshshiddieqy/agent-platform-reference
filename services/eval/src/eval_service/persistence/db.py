from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from eval_service.config import settings

engine: AsyncEngine = create_async_engine(settings.database_url, pool_size=5, max_overflow=0)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncConnection]:
    """No `set_config('app.tenant_id', ...)` here — `eval` has no RLS
    (migration 0006's own docstring: golden sets are org-wide, not
    tenant data), the same reasoning `harness/persistence/db.py`'s
    `admin_session()` already documents for `authz.tool_overrides`.
    """
    async with engine.begin() as conn:
        yield conn
