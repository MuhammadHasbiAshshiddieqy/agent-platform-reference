"""Grant CREATE on the `public` schema to `agent_app`.

Why: LangGraph's `AsyncPostgresSaver.setup()` (§23.1 — PostgresSaver, never
MemorySaver) creates its own checkpoint tables (`checkpoints`,
`checkpoint_writes`, ...) in whatever schema is on the connecting role's
default search_path — `public`, since we don't override it. PostgreSQL 15+
revokes CREATE on `public` from everyone except the schema owner by
default, and `agent_app` is not the owner (`agent` is, per
0008_app_role.py) — so without this grant, checkpointer.setup() fails with
"permission denied for schema public" on every harness boot.

This is a deliberate, narrow exception to §5.6 ("satu service hanya boleh
menulis ke skema miliknya"): LangGraph's checkpoint tables are
orchestration infrastructure, not domain data owned by any of the six
declared schemas — treated the same way Redis holds harness's semantic
cache without being "owned" by a service schema.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-09
"""

from __future__ import annotations

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT CREATE, USAGE ON SCHEMA public TO agent_app")


def downgrade() -> None:
    op.execute("REVOKE CREATE, USAGE ON SCHEMA public FROM agent_app")
