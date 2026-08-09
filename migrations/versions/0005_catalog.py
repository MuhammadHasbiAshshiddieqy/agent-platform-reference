"""catalog schema (§8.5, §28.4 — pgvector/ADR-002): documents, chunks,
ingestion_runs. `catalog.chunks` is the table the M0 DoD explicitly calls
out: RLS here is what turns "forgot the tenant filter" into zero rows
instead of a cross-tenant leak (§7.3, §5.8).

Vector dimension is pinned to 1024 (bge-m3, §28.4) — changing embedding
models means a new `vector(n)` column + backfill + cutover migration, never
an ALTER of this column in place (§11.1).

Column set reconciles two spec passages that don't literally agree: the
CREATE TABLE in §28.4 omits `source_uri`/`lang`/`deleted_at`, but §11.1
lists `source_uri` and `lang` as mandatory per-chunk metadata, and the
hybrid-search query in §28.9 filters on `c.deleted_at` and selects
`c.source_uri` directly off `catalog.chunks`. The executable query wins —
all three columns are included here.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    # See 0002_conversation.py — FORCE is required or the owning role
    # (every service, in this single-role compose) bypasses RLS entirely.
    # This is the one that matters most: catalog.chunks is the table the
    # M0 DoD names explicitly, and it's the one a forgotten filter turns
    # into a cross-tenant RAG leak instead of zero rows (§5.8, §7.3).
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE catalog.documents (
          id             TEXT PRIMARY KEY,
          tenant_id      TEXT NOT NULL,
          source         TEXT NOT NULL,
          source_uri     TEXT NOT NULL,
          title          TEXT,
          content_hash   TEXT NOT NULL,
          acl_group_ids  TEXT[] NOT NULL DEFAULT '{}',
          lang           TEXT,
          ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at     TIMESTAMPTZ,
          UNIQUE (tenant_id, source, source_uri)
        )
        """
    )
    _enable_tenant_rls("catalog.documents")

    op.execute(
        """
        CREATE TABLE catalog.chunks (
          id             TEXT PRIMARY KEY,
          document_id    TEXT NOT NULL REFERENCES catalog.documents(id),
          tenant_id      TEXT NOT NULL,
          acl_group_ids  TEXT[] NOT NULL DEFAULT '{}',
          content        TEXT NOT NULL,
          embedding      vector(1024) NOT NULL,
          content_tsv    tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
          section_path   TEXT,
          source_uri     TEXT,
          lang           TEXT,
          content_hash   TEXT NOT NULL,
          deleted_at     TIMESTAMPTZ,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON catalog.chunks USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ON catalog.chunks USING gin (content_tsv)")
    op.execute("CREATE INDEX ON catalog.chunks (tenant_id)")
    op.execute("CREATE INDEX ON catalog.chunks USING gin (acl_group_ids)")
    _enable_tenant_rls("catalog.chunks")

    op.execute(
        """
        CREATE TABLE catalog.ingestion_runs (
          id             TEXT PRIMARY KEY,
          source         TEXT NOT NULL,
          tenant_id      TEXT NOT NULL,
          status         TEXT NOT NULL,
          docs_seen      INT DEFAULT 0,
          docs_upserted  INT DEFAULT 0,
          docs_deleted   INT DEFAULT 0,
          errors         INT DEFAULT 0,
          started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          finished_at    TIMESTAMPTZ
        )
        """
    )
    _enable_tenant_rls("catalog.ingestion_runs")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog.ingestion_runs")
    op.execute("DROP TABLE IF EXISTS catalog.chunks")
    op.execute("DROP TABLE IF EXISTS catalog.documents")
    op.execute("DROP EXTENSION IF EXISTS vector")
