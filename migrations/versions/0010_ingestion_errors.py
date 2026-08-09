"""catalog.ingestion_errors — §5.11's failure mode names this table
explicitly ("Catat ke catalog.ingestion_errors, lanjutkan, laporkan agregat
di akhir") but the literal DDL in §8.5 never defines it. One failed
document must not abort the whole ingestion run — this table is what lets
a run report "47 of 50 documents ingested, 3 failed" instead of either
losing the failures silently or crashing the batch.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-09
"""

from __future__ import annotations

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels = None
depends_on = None


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE catalog.ingestion_errors (
          id             TEXT PRIMARY KEY,
          ingestion_run_id TEXT NOT NULL REFERENCES catalog.ingestion_runs(id),
          tenant_id      TEXT NOT NULL,
          source_uri     TEXT NOT NULL,
          error_message  TEXT NOT NULL,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON catalog.ingestion_errors (ingestion_run_id)")
    _enable_tenant_rls("catalog.ingestion_errors")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog.ingestion_errors")
