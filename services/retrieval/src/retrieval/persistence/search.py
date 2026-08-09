"""§28.9 — the hybrid query verbatim (dense `<=>` + sparse `tsvector`,
fused with RRF), translated to named parameters for SQLAlchemy but
otherwise unchanged: same CTEs, same RRF constant (60 — standard from the
RRF literature, §28.9 says not to touch it without eval data backing a
change), same `n_cand`/`n_out` shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# ACL is explicit (`c.acl_group_ids && p.acl`) because it's row-level
# authorization, not tenant isolation — RLS doesn't know who the user is.
# `deleted_at IS NULL` excludes ingestion's tombstoned documents (§11.1).
_HYBRID_QUERY = text(
    """
    WITH params AS (
      SELECT
        CAST(:qvec AS vector(1024)) AS qvec,
        CAST(:qtext AS text)        AS qtext,
        CAST(:acl AS text[])        AS acl,
        CAST(:n_cand AS int)        AS n_cand,
        CAST(:n_out AS int)         AS n_out
    ),
    dense AS (
      SELECT c.id, ROW_NUMBER() OVER (ORDER BY c.embedding <=> p.qvec) AS rank
      FROM catalog.chunks c, params p
      WHERE c.acl_group_ids && p.acl
        AND c.deleted_at IS NULL
      ORDER BY c.embedding <=> p.qvec
      LIMIT (SELECT n_cand FROM params)
    ),
    sparse AS (
      SELECT c.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(c.content_tsv, q) DESC) AS rank
      FROM catalog.chunks c, params p, plainto_tsquery('simple', p.qtext) q
      WHERE c.content_tsv @@ q
        AND c.acl_group_ids && p.acl
        AND c.deleted_at IS NULL
      ORDER BY ts_rank_cd(c.content_tsv, q) DESC
      LIMIT (SELECT n_cand FROM params)
    ),
    fused AS (
      SELECT id, SUM(1.0 / (60 + rank)) AS rrf_score
      FROM (SELECT id, rank FROM dense UNION ALL SELECT id, rank FROM sparse) u
      GROUP BY id
    )
    SELECT c.id, c.document_id, c.content, c.section_path, c.source_uri, f.rrf_score
    FROM fused f
    JOIN catalog.chunks c ON c.id = f.id
    ORDER BY f.rrf_score DESC
    LIMIT (SELECT n_out FROM params)
    """
)


@dataclass
class HybridSearchRow:
    chunk_id: str
    document_id: str
    content: str
    section_path: str | None
    source_uri: str
    rrf_score: float


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(repr(v) for v in values) + "]"


async def hybrid_search(
    conn: AsyncConnection,
    *,
    query_vector: list[float],
    query_text: str,
    acl_group_ids: list[str],
    n_candidates: int,
    n_out: int,
) -> list[HybridSearchRow]:
    # §28.4 — without this, a selective tenant/ACL filter can make HNSW
    # return candidates that all get filtered away, silently tanking
    # recall with no error. Session-scoped, inside the same transaction
    # tenant_session() already opened.
    await conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await conn.execute(text("SET LOCAL hnsw.max_scan_tuples = 20000"))

    result = await conn.execute(
        _HYBRID_QUERY,
        {
            "qvec": vector_literal(query_vector),
            "qtext": query_text,
            "acl": acl_group_ids,
            "n_cand": n_candidates,
            "n_out": n_out,
        },
    )
    return [
        HybridSearchRow(
            chunk_id=row.id,
            document_id=row.document_id,
            content=row.content,
            section_path=row.section_path,
            source_uri=row.source_uri,
            rrf_score=row.rrf_score,
        )
        for row in result
    ]
