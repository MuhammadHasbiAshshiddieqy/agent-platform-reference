"""Create the six logical schemas (§5.6). `litellm` is not among them — it
lives in its own database (deploy/postgres/init.sql), owned by LiteLLM
itself, and this repo never migrates it.

Revision ID: 0001
Revises:
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None

SCHEMAS = ["conversation", "audit", "catalog", "jobs", "eval", "authz"]


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
