"""eval.judge_cache — §13.5's "Cache hasil juri berkunci (item_id,
sha256(response), judge_model_version, metric)". Not in 0006's original
DDL (that migration predates M8's actual eval-service build and only
carried the schema §13.2/§13.9 already specified); this is the one new
table M8 itself needed once the judge-caching requirement was actually
implemented. No RLS — same "org-wide, not tenant data" reasoning as the
rest of the `eval` schema (0006's own docstring).

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-09
"""

from __future__ import annotations

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eval.judge_cache (
          item_id             TEXT NOT NULL,
          response_hash       TEXT NOT NULL,
          judge_model_version TEXT NOT NULL,
          metric              TEXT NOT NULL,
          score               DOUBLE PRECISION NOT NULL,
          reason               TEXT,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (item_id, response_hash, judge_model_version, metric)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eval.judge_cache")
