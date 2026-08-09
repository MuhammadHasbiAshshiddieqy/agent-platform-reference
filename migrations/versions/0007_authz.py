"""authz schema + audit.authz_decisions (§22.8 — RBAC). `deny` decisions are
recorded with the same weight as `allow` — a spike of denials for one user
is itself a signal worth auditing (§22.8).

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE authz.tool_overrides (
          tool_name    TEXT PRIMARY KEY,
          disabled     BOOLEAN NOT NULL DEFAULT false,
          reason       TEXT,
          changed_by   TEXT NOT NULL,
          changed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE audit.authz_decisions (
          id              TEXT PRIMARY KEY,
          run_id          TEXT NOT NULL,
          trace_id        TEXT NOT NULL,
          tenant_id       TEXT NOT NULL,
          actor_user_id   TEXT NOT NULL,
          actor_roles     TEXT[] NOT NULL,
          agent_id        TEXT NOT NULL,
          audience        TEXT NOT NULL CHECK (audience IN ('internal','external')),
          tool_name       TEXT NOT NULL,
          decision        TEXT NOT NULL CHECK (decision IN ('allow','deny')),
          deny_reason     TEXT,
          missing_permissions TEXT[],
          data_scope      TEXT,
          scope_satisfied BOOLEAN,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON audit.authz_decisions (tenant_id, created_at DESC)")
    op.execute("CREATE INDEX ON audit.authz_decisions (actor_user_id, decision, created_at DESC)")
    op.execute("ALTER TABLE audit.authz_decisions ENABLE ROW LEVEL SECURITY")
    # See migrations/versions/0002_conversation.py — FORCE is required or
    # the owning role (every service, single-role compose) bypasses RLS.
    op.execute("ALTER TABLE audit.authz_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON audit.authz_decisions "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit.authz_decisions")
    op.execute("DROP TABLE IF EXISTS authz.tool_overrides")
