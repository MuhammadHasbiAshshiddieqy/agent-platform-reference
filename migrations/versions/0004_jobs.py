"""jobs schema (§8.5): async_jobs, idempotency_keys.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    # See 0002_conversation.py — FORCE is required or the owning role
    # (every service, in this single-role compose) bypasses RLS entirely.
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE jobs.async_jobs (
          id              TEXT PRIMARY KEY,
          tenant_id       TEXT NOT NULL,
          user_id         TEXT NOT NULL,
          trace_id        TEXT NOT NULL,
          priority        TEXT NOT NULL DEFAULT 'standard',
          payload         JSONB NOT NULL,
          status          TEXT NOT NULL,
          attempts        INT NOT NULL DEFAULT 0,
          result          JSONB,
          error           JSONB,
          callback_url    TEXT,
          callback_status TEXT,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          started_at      TIMESTAMPTZ,
          completed_at    TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ON jobs.async_jobs (tenant_id, status, created_at DESC)")
    _enable_tenant_rls("jobs.async_jobs")

    op.execute(
        """
        CREATE TABLE jobs.idempotency_keys (
          tenant_id       TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          request_hash    TEXT NOT NULL,
          response_body   JSONB,
          status_code     INT,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, idempotency_key)
        )
        """
    )
    _enable_tenant_rls("jobs.idempotency_keys")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS jobs.idempotency_keys")
    op.execute("DROP TABLE IF EXISTS jobs.async_jobs")
