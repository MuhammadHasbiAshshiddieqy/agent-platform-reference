"""audit schema (§8.5) core tables: tool_invocations, mutation_requests,
guardrail_events. `audit.authz_decisions` follows in 0007 alongside RBAC
(§22.8) since it's introduced together with the authz schema.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
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
        CREATE TABLE audit.tool_invocations (
          id              TEXT PRIMARY KEY,
          run_id          TEXT NOT NULL,
          trace_id        TEXT NOT NULL,
          tenant_id       TEXT NOT NULL,
          tool_name       TEXT NOT NULL,
          tool_kind       TEXT NOT NULL CHECK (tool_kind IN ('readonly','mutation')),
          arguments       JSONB NOT NULL,
          result_summary  JSONB,
          status          TEXT NOT NULL,
          latency_ms      INT,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON audit.tool_invocations (run_id)")
    _enable_tenant_rls("audit.tool_invocations")

    op.execute(
        """
        CREATE TABLE audit.mutation_requests (
          id                TEXT PRIMARY KEY,
          run_id            TEXT NOT NULL,
          trace_id          TEXT NOT NULL,
          tenant_id         TEXT NOT NULL,
          actor_user_id     TEXT NOT NULL,
          action_name       TEXT NOT NULL,
          risk_level        TEXT NOT NULL,
          preview_payload   JSONB NOT NULL,
          approval_id       TEXT,
          approved_by       TEXT,
          approved_at       TIMESTAMPTZ,
          idempotency_key   TEXT NOT NULL,
          status            TEXT NOT NULL CHECK (status IN
                             ('previewed','awaiting_approval','approved','rejected',
                              'executed','failed','expired')),
          executed_at       TIMESTAMPTZ,
          business_ref      TEXT,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    _enable_tenant_rls("audit.mutation_requests")

    op.execute(
        """
        CREATE TABLE audit.guardrail_events (
          id           TEXT PRIMARY KEY,
          run_id       TEXT NOT NULL,
          trace_id     TEXT NOT NULL,
          tenant_id    TEXT NOT NULL,
          stage        TEXT NOT NULL CHECK (stage IN ('input','output')),
          rule_id      TEXT NOT NULL,
          severity     TEXT NOT NULL,
          action_taken TEXT NOT NULL CHECK (action_taken IN ('allow','redact','block','flag')),
          detail       JSONB,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    _enable_tenant_rls("audit.guardrail_events")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit.guardrail_events")
    op.execute("DROP TABLE IF EXISTS audit.mutation_requests")
    op.execute("DROP TABLE IF EXISTS audit.tool_invocations")
