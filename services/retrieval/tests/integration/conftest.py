"""§28.9's hybrid-search tests need a real pgvector/postgres with every
migration applied — same testcontainers pattern as tests/security/conftest.py
(M0). Deliberately does NOT import `retrieval.config` / `retrieval.persistence.db`:
those read `DATABASE_URL`/`MODEL_ROUTER_KEY` from the environment at import
time (pydantic-settings), which this test suite never sets — it drives
`retrieval.persistence.search.hybrid_search()` directly against its own
testcontainer connection instead of the service's configured engine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "migrations"


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    import os

    os.environ["APP_DB_PASSWORD"] = "test-app-db-password"
    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as pg:
        os.environ["DATABASE_URL"] = pg.get_connection_url()
        cfg = Config(str(MIGRATIONS_DIR / "alembic.ini"))
        command.upgrade(cfg, "head")
        yield pg


@pytest.fixture(scope="session")
def engine(pg_container: PostgresContainer) -> Iterator[AsyncEngine]:
    # NullPool — a session-scoped engine handing out pooled asyncpg
    # connections across pytest-asyncio's per-test event loops corrupts
    # them ("cannot perform operation: another operation is in progress"):
    # asyncpg connections are bound to the event loop they were opened on,
    # and pytest-asyncio's default loop scope is function, not session.
    # NullPool opens a fresh physical connection for every `engine.begin()`
    # instead of reusing one from a possibly-dead loop.
    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(5432)
    dsn = f"postgresql+asyncpg://agent_app:test-app-db-password@{host}:{port}/{pg_container.dbname}"
    eng = create_async_engine(dsn, poolclass=NullPool)
    yield eng


@pytest_asyncio.fixture()
async def conn(engine: AsyncEngine, tenant_id: str) -> AsyncIterator[AsyncConnection]:
    """Opens the transaction and sets `app.tenant_id` — the RLS-off test
    overrides this with its own connection that skips the set_config
    call, so it lives outside this fixture."""
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
        )
        yield connection


@pytest.fixture()
def tenant_id() -> str:
    return "tnt_hybrid_test"
