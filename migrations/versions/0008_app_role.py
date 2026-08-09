"""Non-superuser application role (`agent_app`).

Why this migration exists: every RLS policy so far uses FORCE ROW LEVEL
SECURITY (§23.2k), but FORCE only binds a non-superuser table *owner* —
PostgreSQL superusers bypass row security unconditionally, FORCE or not.
`agent` (POSTGRES_USER, deploy/postgres/init.sql) is the initdb bootstrap
role, which is a superuser. If application services connected as `agent`,
every tenant_isolation policy in this repo would be silent dead code — the
exact "senyap" (silent) failure mode §23.2k warns about, just one layer up
from the connection-pooling hazard it actually describes.

`agent` stays reserved for migrations (DDL needs owner/superuser rights
anyway). From M1 onward, every service's DATABASE_URL must use `agent_app`,
not `agent` — see .env.example / Makefile.

This grants broadly across all six schemas rather than one least-privilege
role per service — a deliberate POC simplification (consistent with
mock-business-api being one container for three domains, §5.12), not
something to imitate at Mekari's actual production scale.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08
"""

from __future__ import annotations

import os

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels = None
depends_on = None

SCHEMAS = ["conversation", "audit", "catalog", "jobs", "eval", "authz"]


def upgrade() -> None:
    password = os.environ.get("APP_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "APP_DB_PASSWORD must be set to run this migration (creates the "
            "non-superuser `agent_app` role every service connects as from "
            "M1 onward). See .env.example."
        )

    escaped_password = password.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_app') THEN
            CREATE ROLE agent_app WITH LOGIN PASSWORD '{escaped_password}'
              NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
          END IF;
        END
        $$
        """
    )
    # current_database(), not a literal "agent_platform" — this migration
    # also runs against whatever ephemeral database name testcontainers
    # hands tests/security's conftest.py.
    op.execute(
        "DO $$ BEGIN "
        "EXECUTE format('GRANT CONNECT ON DATABASE %I TO agent_app', current_database()); "
        "END $$"
    )

    for schema in SCHEMAS:
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO agent_app")
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO agent_app"
        )
        # Tables created by later migrations (still run as whichever role
        # is executing this migration — `agent` in the real deploy, an
        # ephemeral superuser under testcontainers) are covered
        # automatically. current_user, not a literal "agent": this
        # migration also runs under tests/security's throwaway container.
        op.execute(
            "DO $$ BEGIN "
            "EXECUTE format("
            "'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agent_app', "
            f"current_user, '{schema}'"
            "); END $$"
        )


def downgrade() -> None:
    for schema in SCHEMAS:
        op.execute(
            "DO $$ BEGIN "
            "EXECUTE format("
            "'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I "
            "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM agent_app', "
            f"current_user, '{schema}'"
            "); END $$"
        )
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM agent_app")
        op.execute(f"REVOKE USAGE ON SCHEMA {schema} FROM agent_app")
    op.execute(
        "DO $$ BEGIN "
        "EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM agent_app', current_database()); "
        "END $$"
    )
    op.execute("DROP ROLE IF EXISTS agent_app")
