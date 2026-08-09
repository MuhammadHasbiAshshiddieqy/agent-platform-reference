"""§7.3 / §23.2k — cross-tenant isolation must hold at the database layer,
independent of any application code remembering to filter by tenant_id.

Every test here uses the non-superuser `agent_app` role (see conftest.py)
except one, which deliberately uses the migration superuser to document why
`agent_app` has to exist at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
import pytest


def _zero_vector(dim: int = 1024) -> str:
    return "[" + ",".join(["0"] * dim) + "]"


def _run(
    conn: psycopg.Connection, tenant_id: str | None, sql: str, params: Sequence[Any] = ()
) -> list[tuple[Any, ...]] | None:
    """One statement per transaction — the §23.2k pattern: transaction-
    scoped tenant setting, never session-scoped. `tenant_id=None` means
    "forgot to set it", on purpose.

    Uses `SELECT set_config('app.tenant_id', %s, true)`, not
    `SET LOCAL app.tenant_id = %s` — PostgreSQL's `SET` grammar takes a
    literal, not a bind parameter; `SET LOCAL x = %s` is a syntax error
    with every driver (confirmed empirically here). This is the same bug
    §23.2k's own `tenant_session()` pseudocode has (`text("SET LOCAL
    app.tenant_id = :tid")`) — worth fixing when the harness implements
    that helper for real in M1. `set_config(..., true)` is the
    parameter-safe equivalent of `SET LOCAL` (third arg `true` = local to
    the transaction).
    """
    with conn.cursor() as cur:
        if tenant_id is not None:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description is not None else None
    conn.commit()
    return rows


@pytest.fixture()
def conn(app_role_dsn: str) -> psycopg.Connection:
    with psycopg.connect(app_role_dsn) as connection:
        yield connection


INSERT_CONVERSATION = (
    "INSERT INTO conversation.conversations (id, tenant_id, user_id, agent_id) "
    "VALUES (%s, %s, %s, %s)"
)
INSERT_DOCUMENT = (
    "INSERT INTO catalog.documents (id, tenant_id, source, source_uri, content_hash) "
    "VALUES (%s, %s, %s, %s, %s)"
)
INSERT_CHUNK = (
    "INSERT INTO catalog.chunks (id, document_id, tenant_id, content, embedding, content_hash) "
    "VALUES (%s, %s, %s, %s, %s::vector, %s)"
)


class TestConversationsTenantIsolation:
    def test_cross_tenant_read_returns_zero_rows(self, conn: psycopg.Connection) -> None:
        _run(
            conn, "tnt_rls_a", INSERT_CONVERSATION, ("conv_rls_a", "tnt_rls_a", "usr_a", "agent_x")
        )
        _run(
            conn, "tnt_rls_b", INSERT_CONVERSATION, ("conv_rls_b", "tnt_rls_b", "usr_b", "agent_x")
        )

        select = "SELECT id FROM conversation.conversations WHERE id IN (%s, %s)"
        ids = ("conv_rls_a", "conv_rls_b")

        rows_a = _run(conn, "tnt_rls_a", select, ids)
        assert [r[0] for r in rows_a] == ["conv_rls_a"]

        rows_b = _run(conn, "tnt_rls_b", select, ids)
        assert [r[0] for r in rows_b] == ["conv_rls_b"]

    def test_forgetting_set_local_returns_zero_rows_not_all_rows(
        self, conn: psycopg.Connection
    ) -> None:
        """§5.8/§7.3's central promise: a forgotten filter is zero rows, not
        another tenant's data."""
        _run(
            conn, "tnt_rls_forget", INSERT_CONVERSATION, ("conv_forget", "tnt_rls_forget", "u", "a")
        )
        rows = _run(
            conn, None, "SELECT id FROM conversation.conversations WHERE id = %s", ("conv_forget",)
        )
        assert rows == []


class TestCatalogChunksTenantIsolation:
    """catalog.chunks is the table the M0 DoD names explicitly — a
    forgotten filter here is a cross-tenant RAG leak (§5.8, §7.3), not
    merely a correctness bug."""

    def test_cross_tenant_read_returns_zero_rows(self, conn: psycopg.Connection) -> None:
        embedding = _zero_vector()

        _run(
            conn,
            "tnt_rls_a",
            INSERT_DOCUMENT,
            ("doc_rls_a", "tnt_rls_a", "seed", "file://a", "hash_a"),
        )
        _run(
            conn,
            "tnt_rls_a",
            INSERT_CHUNK,
            ("chk_rls_a", "doc_rls_a", "tnt_rls_a", "tenant A content", embedding, "chash_a"),
        )

        _run(
            conn,
            "tnt_rls_b",
            INSERT_DOCUMENT,
            ("doc_rls_b", "tnt_rls_b", "seed", "file://b", "hash_b"),
        )
        _run(
            conn,
            "tnt_rls_b",
            INSERT_CHUNK,
            ("chk_rls_b", "doc_rls_b", "tnt_rls_b", "tenant B content", embedding, "chash_b"),
        )

        select = "SELECT id FROM catalog.chunks WHERE id IN (%s, %s)"
        ids = ("chk_rls_a", "chk_rls_b")

        rows_a = _run(conn, "tnt_rls_a", select, ids)
        assert [r[0] for r in rows_a] == ["chk_rls_a"]

        rows_b = _run(conn, "tnt_rls_b", select, ids)
        assert [r[0] for r in rows_b] == ["chk_rls_b"]

    def test_vector_knn_search_respects_tenant_filter(self, conn: psycopg.Connection) -> None:
        """Regression guard for §28.4/§28.9's central hazard: a KNN query
        has no `tenant_id` literal in its WHERE clause — RLS supplies the
        filter entirely. Tenant A's chunk is the closer embedding match by
        construction; if RLS were bypassed or misapplied, it would leak
        into tenant B's results anyway."""
        closer = "[" + ",".join(["0.001"] * 1024) + "]"
        farther = _zero_vector()

        # Distinct tenant ids from TestCatalogChunksTenantIsolation's other
        # test — this class shares one database across the whole session
        # (conftest.py's container is session-scoped), so id collisions
        # here would silently pull in rows a previous test inserted.
        _run(
            conn,
            "tnt_knn_a",
            INSERT_DOCUMENT,
            ("doc_knn_a", "tnt_knn_a", "seed", "file://knn-a", "h1"),
        )
        _run(
            conn,
            "tnt_knn_a",
            INSERT_CHUNK,
            ("chk_knn_a", "doc_knn_a", "tnt_knn_a", "closer match, wrong tenant", closer, "ch1"),
        )
        _run(
            conn,
            "tnt_knn_b",
            INSERT_DOCUMENT,
            ("doc_knn_b", "tnt_knn_b", "seed", "file://knn-b", "h2"),
        )
        _run(
            conn,
            "tnt_knn_b",
            INSERT_CHUNK,
            (
                "chk_knn_b",
                "doc_knn_b",
                "tnt_knn_b",
                "farther match, correct tenant",
                farther,
                "ch2",
            ),
        )

        rows = _run(
            conn,
            "tnt_knn_b",
            "SELECT id FROM catalog.chunks ORDER BY embedding <=> %s::vector LIMIT 5",
            (closer,),
        )
        ids = [r[0] for r in rows]
        assert "chk_knn_a" not in ids
        assert ids == ["chk_knn_b"]


def test_superuser_bypasses_rls_documenting_why_agent_app_exists(superuser_dsn: str) -> None:
    """Not a vulnerability in the schema — a fact about PostgreSQL this repo
    has to design around (§23.2k). PostgreSQL superusers bypass row
    security unconditionally, FORCE ROW LEVEL SECURITY or not. Connecting
    as the migration role (`agent`, a superuser) sees every tenant's rows
    regardless of SET LOCAL — which is exactly why every application
    service must connect as `agent_app` (0008_app_role.py) instead.
    """
    with psycopg.connect(superuser_dsn) as conn:
        _run(conn, "tnt_super_a", INSERT_CONVERSATION, ("conv_super_a", "tnt_super_a", "u", "a"))
        _run(conn, "tnt_super_b", INSERT_CONVERSATION, ("conv_super_b", "tnt_super_b", "u", "a"))

        rows = _run(
            conn,
            "tnt_super_a",
            "SELECT id FROM conversation.conversations WHERE id IN (%s, %s)",
            ("conv_super_a", "conv_super_b"),
        )
        assert {r[0] for r in rows} == {"conv_super_a", "conv_super_b"}
