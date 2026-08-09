"""conversation schema (§8.5): conversations, messages, agent_runs.

Every table here carries tenant_id and gets RLS (§7.3) — a query that
forgets to `SET LOCAL app.tenant_id` returns zero rows, not another
tenant's rows.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    # FORCE, not just ENABLE: without it, RLS does not apply to the table
    # owner — and every service connects as the same `agent` role that
    # owns these tables (single-role compose, §17/§28.8). Without FORCE,
    # every "tenant_isolation" policy below is silently bypassed for the
    # exact role that's supposed to be bound by it (§23.2k).
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversation.conversations (
          id              TEXT PRIMARY KEY,
          tenant_id       TEXT NOT NULL,
          user_id         TEXT NOT NULL,
          agent_id        TEXT NOT NULL,
          title           TEXT,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          archived_at     TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ON conversation.conversations (tenant_id, user_id, updated_at DESC)")
    _enable_tenant_rls("conversation.conversations")

    op.execute(
        """
        CREATE TABLE conversation.messages (
          id              TEXT PRIMARY KEY,
          conversation_id TEXT NOT NULL REFERENCES conversation.conversations(id),
          tenant_id       TEXT NOT NULL,
          role            TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
          content         JSONB NOT NULL,
          citations       JSONB NOT NULL DEFAULT '[]',
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON conversation.messages (conversation_id, created_at)")
    _enable_tenant_rls("conversation.messages")

    op.execute(
        """
        CREATE TABLE conversation.agent_runs (
          id                TEXT PRIMARY KEY,
          trace_id          TEXT NOT NULL,
          tenant_id         TEXT NOT NULL,
          conversation_id   TEXT REFERENCES conversation.conversations(id),
          agent_id          TEXT NOT NULL,
          agent_version     TEXT NOT NULL,
          prompt_version    TEXT NOT NULL,
          execution_mode    TEXT NOT NULL CHECK (execution_mode IN ('sync','async')),
          status            TEXT NOT NULL,
          input_tokens      INT NOT NULL DEFAULT 0,
          output_tokens     INT NOT NULL DEFAULT 0,
          cost_usd          NUMERIC(12,6) NOT NULL DEFAULT 0,
          cache_hit         BOOLEAN NOT NULL DEFAULT false,
          degraded          TEXT[] NOT NULL DEFAULT '{}',
          latency_ms        INT,
          error_code        TEXT,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON conversation.agent_runs (tenant_id, created_at DESC)")
    op.execute("CREATE INDEX ON conversation.agent_runs (trace_id)")
    _enable_tenant_rls("conversation.agent_runs")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversation.agent_runs")
    op.execute("DROP TABLE IF EXISTS conversation.messages")
    op.execute("DROP TABLE IF EXISTS conversation.conversations")
