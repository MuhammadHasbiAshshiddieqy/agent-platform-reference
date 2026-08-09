"""Spins up one real pgvector/postgres container for the whole test session,
runs every migration in migrations/versions/ against it, and hands tests two
DSNs off the same instance:

- `app_role_dsn` — the non-superuser `agent_app` role every real test uses.
  Connecting as the migration superuser would prove nothing: superusers
  bypass RLS unconditionally, FORCE ROW LEVEL SECURITY or not (§23.2k).
- `superuser_dsn` — used by exactly one test (test_superuser_bypasses_rls)
  that documents *why* agent_app has to exist, so a future contributor
  can't "simplify" this away without the test telling them why not to.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    app_password = secrets.token_hex(16)
    os.environ["APP_DB_PASSWORD"] = app_password

    with PostgresContainer("pgvector/pgvector:pg16", driver="psycopg") as pg:
        os.environ["DATABASE_URL"] = pg.get_connection_url()
        cfg = Config(str(MIGRATIONS_DIR / "alembic.ini"))
        command.upgrade(cfg, "head")
        pg.app_password = app_password  # type: ignore[attr-defined]
        yield pg


@pytest.fixture(scope="session")
def app_role_dsn(pg_container: PostgresContainer) -> str:
    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(5432)
    password = pg_container.app_password  # type: ignore[attr-defined]
    return f"postgresql://agent_app:{password}@{host}:{port}/{pg_container.dbname}"


@pytest.fixture(scope="session")
def superuser_dsn(pg_container: PostgresContainer) -> str:
    return pg_container.get_connection_url().replace("postgresql+psycopg://", "postgresql://")
