"""eval schema — built directly from the *extended* §13.2 version of
`eval.items` (the version that supersedes §8.5's bare version; there is no
earlier bare version to migrate away from since this repo starts fresh),
plus `eval.runs` with the `is_baseline`/`dataset_version` columns from
§13.9 already present, plus `eval.baseline_changes`.

No tenant_id on any table here — eval datasets are org-wide golden sets,
not tenant data (§13.2) — so no RLS in this migration.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eval.datasets (
          id          TEXT PRIMARY KEY,
          name        TEXT NOT NULL UNIQUE,
          agent_id    TEXT NOT NULL,
          description TEXT,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE eval.items (
          id                     TEXT PRIMARY KEY,
          dataset_id             TEXT NOT NULL REFERENCES eval.datasets(id),
          question               TEXT NOT NULL,
          ground_truth           TEXT,
          contexts               JSONB,

          actor_user_id          TEXT NOT NULL,
          actor_role              TEXT NOT NULL,
          actor_acl_group_ids    TEXT[] NOT NULL DEFAULT '{}',

          expected_required_tools TEXT[] NOT NULL DEFAULT '{}',
          forbidden_tools         TEXT[] NOT NULL DEFAULT '{}',
          allowed_mutations       TEXT[] NOT NULL DEFAULT '{}',
          should_refuse           BOOLEAN NOT NULL DEFAULT false,
          forbidden_output_terms  TEXT[] NOT NULL DEFAULT '{}',
          allowed_pii             TEXT[] NOT NULL DEFAULT '{}',
          expects_citation        BOOLEAN NOT NULL DEFAULT true,

          tags                   TEXT[] NOT NULL DEFAULT '{}',
          difficulty             TEXT,
          source_trace           TEXT,
          created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON eval.items (dataset_id, tags)")

    op.execute(
        """
        CREATE TABLE eval.runs (
          id             TEXT PRIMARY KEY,
          dataset_id     TEXT NOT NULL REFERENCES eval.datasets(id),
          agent_id       TEXT NOT NULL,
          agent_version  TEXT NOT NULL,
          prompt_version TEXT NOT NULL,
          model_alias    TEXT NOT NULL,
          git_sha        TEXT NOT NULL,
          dataset_version TEXT NOT NULL,
          scores         JSONB NOT NULL,
          passed         BOOLEAN NOT NULL,
          is_baseline    BOOLEAN NOT NULL DEFAULT false,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # §13.9 — one active baseline per (dataset_id, agent_id).
    op.execute("CREATE UNIQUE INDEX ON eval.runs (dataset_id, agent_id) WHERE is_baseline")

    op.execute(
        """
        CREATE TABLE eval.baseline_changes (
          id              TEXT PRIMARY KEY,
          dataset_id      TEXT NOT NULL REFERENCES eval.datasets(id),
          agent_id        TEXT NOT NULL,
          from_run_id     TEXT REFERENCES eval.runs(id),
          to_run_id       TEXT NOT NULL REFERENCES eval.runs(id),
          reason          TEXT NOT NULL,
          changed_by      TEXT NOT NULL,
          auto            BOOLEAN NOT NULL DEFAULT false,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eval.baseline_changes")
    op.execute("DROP TABLE IF EXISTS eval.runs")
    op.execute("DROP TABLE IF EXISTS eval.items")
    op.execute("DROP TABLE IF EXISTS eval.datasets")
