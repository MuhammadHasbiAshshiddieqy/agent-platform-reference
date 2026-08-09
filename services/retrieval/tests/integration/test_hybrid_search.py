"""§28.9 — the five mandatory hybrid-search tests, plus the falsifiability
check the section calls for: a dense-only and a sparse-only variant of the
same query prove each path genuinely contributes, not just that the
combined query returns *something*.
"""

from __future__ import annotations

import random
import uuid

import pytest
from retrieval.persistence.search import hybrid_search, vector_literal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

DIM = 1024
BASE_VECTOR = [0.1] * DIM
FAR_VECTOR = [-0.1] * DIM  # cosine-opposite of BASE_VECTOR


def _close_vector(seed: int) -> list[float]:
    rng = random.Random(seed)
    return [0.1 + rng.uniform(-0.002, 0.002) for _ in range(DIM)]


def _noise_vector(seed: int) -> list[float]:
    rng = random.Random(seed)
    return [0.1 + rng.uniform(-0.05, 0.05) for _ in range(DIM)]


# Isolated single-path variants of the §28.9 query, for the falsifiability
# proof only — the real service (retrieval.persistence.search) always runs
# the fused version; these exist so a test can show turning a path off
# breaks exactly the case that path is responsible for.
_DENSE_ONLY_QUERY = text(
    """
    SELECT c.id FROM catalog.chunks c
    WHERE c.acl_group_ids && CAST(:acl AS text[]) AND c.deleted_at IS NULL
    ORDER BY c.embedding <=> CAST(:qvec AS vector(1024))
    LIMIT :n
    """
)
_SPARSE_ONLY_QUERY = text(
    """
    SELECT c.id FROM catalog.chunks c, plainto_tsquery('simple', CAST(:qtext AS text)) q
    WHERE c.content_tsv @@ q
      AND c.acl_group_ids && CAST(:acl AS text[]) AND c.deleted_at IS NULL
    ORDER BY ts_rank_cd(c.content_tsv, q) DESC
    LIMIT :n
    """
)


async def _insert_document(
    conn: AsyncConnection, *, document_id: str, tenant_id: str, source_uri: str, acl: list[str]
) -> None:
    await conn.execute(
        text(
            "INSERT INTO catalog.documents "
            "(id, tenant_id, source, source_uri, content_hash, acl_group_ids) "
            "VALUES (:id, :tenant_id, 'test', :source_uri, 'hash', :acl) "
            "ON CONFLICT (tenant_id, source, source_uri) DO NOTHING"
        ),
        {"id": document_id, "tenant_id": tenant_id, "source_uri": source_uri, "acl": acl},
    )


async def _insert_dense_decoys(
    conn: AsyncConnection, *, document_id: str, tenant_id: str, acl: list[str], count: int = 10
) -> None:
    """Chunks embedded close to BASE_VECTOR, sharing no tokens with any
    test query — pure dense-path competition, so a "dense-only excludes
    it" assertion means something (with zero competing candidates, an
    isolated `ORDER BY distance LIMIT n` trivially returns whatever exists,
    however far it is)."""
    rows = [
        {
            "id": f"chk_decoy_{i}_{uuid.uuid4().hex[:6]}",
            "document_id": document_id,
            "tenant_id": tenant_id,
            "acl": acl,
            "content": f"materi pelatihan onboarding modul {i} tidak berkaitan",
            "embedding": vector_literal(_close_vector(seed=1000 + i)),
            "content_hash": f"decoy-{i}",
        }
        for i in range(count)
    ]
    await conn.execute(
        text(
            "INSERT INTO catalog.chunks "
            "(id, document_id, tenant_id, acl_group_ids, content, embedding, source_uri, "
            "content_hash) "
            "VALUES (:id, :document_id, :tenant_id, :acl, :content, "
            "CAST(:embedding AS vector), "
            "'file://decoy', :content_hash)"
        ),
        rows,
    )


async def _insert_chunk(
    conn: AsyncConnection,
    *,
    chunk_id: str,
    document_id: str,
    tenant_id: str,
    acl: list[str],
    content: str,
    embedding: list[float],
) -> None:
    await conn.execute(
        text(
            "INSERT INTO catalog.chunks "
            "(id, document_id, tenant_id, acl_group_ids, content, embedding, source_uri, "
            "content_hash) "
            "VALUES (:id, :document_id, :tenant_id, :acl, :content, "
            "CAST(:embedding AS vector), "
            "'file://test', :content_hash)"
        ),
        {
            "id": chunk_id,
            "document_id": document_id,
            "tenant_id": tenant_id,
            "acl": acl,
            "content": content,
            "embedding": vector_literal(embedding),
            "content_hash": chunk_id,
        },
    )


@pytest.mark.asyncio
class TestHybridSearchMandatorySuite:
    async def test_code_query_matches_via_sparse_path_only(
        self, conn: AsyncConnection, tenant_id: str
    ) -> None:
        """§28.9 row 1: a document-code query ("SOP-PR-014") must be found
        — it has no semantic neighborhood a dense embedding would place it
        in, so only the sparse (BM25-ish) path can surface it."""
        doc_id, chunk_id = f"doc_{uuid.uuid4().hex[:8]}", f"chk_{uuid.uuid4().hex[:8]}"
        await _insert_document(
            conn, document_id=doc_id, tenant_id=tenant_id, source_uri="sop.md", acl=["grp_hr"]
        )
        await _insert_chunk(
            conn,
            chunk_id=chunk_id,
            document_id=doc_id,
            tenant_id=tenant_id,
            acl=["grp_hr"],
            content="Prosedur ini merujuk pada dokumen kode SOP-PR-014 untuk approval.",
            embedding=FAR_VECTOR,  # deliberately NOT near the query embedding
        )
        # Competition for dense's top-N — without this, an isolated
        # dense-only query trivially "finds" the target since it's the
        # only candidate that exists at all, regardless of distance.
        await _insert_dense_decoys(conn, document_id=doc_id, tenant_id=tenant_id, acl=["grp_hr"])

        rows = await hybrid_search(
            conn,
            query_vector=BASE_VECTOR,
            query_text="SOP-PR-014",
            acl_group_ids=["grp_hr"],
            n_candidates=50,
            n_out=20,
        )
        assert chunk_id in [r.chunk_id for r in rows]

        # Falsifiability: dense-only must NOT find it (proves dense alone
        # would have failed this exact case).
        dense_only = await conn.execute(
            _DENSE_ONLY_QUERY, {"acl": ["grp_hr"], "qvec": vector_literal(BASE_VECTOR), "n": 5}
        )
        assert chunk_id not in [r[0] for r in dense_only]

        sparse_only = await conn.execute(
            _SPARSE_ONLY_QUERY, {"acl": ["grp_hr"], "qtext": "SOP-PR-014", "n": 5}
        )
        assert chunk_id in [r[0] for r in sparse_only]

    async def test_paraphrase_query_matches_via_dense_path_only(
        self, conn: AsyncConnection, tenant_id: str
    ) -> None:
        """§28.9 row 2: a conceptual paraphrase with zero keyword overlap
        must still be found — only the dense path can bridge that gap."""
        doc_id, chunk_id = f"doc_{uuid.uuid4().hex[:8]}", f"chk_{uuid.uuid4().hex[:8]}"
        await _insert_document(
            conn,
            document_id=doc_id,
            tenant_id=tenant_id,
            source_uri="leave.md",
            acl=["grp_all_staff"],
        )
        await _insert_chunk(
            conn,
            chunk_id=chunk_id,
            document_id=doc_id,
            tenant_id=tenant_id,
            acl=["grp_all_staff"],
            content="Kuota istirahat tahunan pegawai adalah dua belas hari kerja.",
            embedding=_close_vector(seed=1),  # semantically close to the query
        )

        rows = await hybrid_search(
            conn,
            query_vector=BASE_VECTOR,
            query_text="berapa jumlah cuti tahunan karyawan",  # shares no tokens with the chunk
            acl_group_ids=["grp_all_staff"],
            n_candidates=50,
            n_out=20,
        )
        assert chunk_id in [r.chunk_id for r in rows]

        sparse_only = await conn.execute(
            _SPARSE_ONLY_QUERY,
            {"acl": ["grp_all_staff"], "qtext": "berapa jumlah cuti tahunan karyawan", "n": 5},
        )
        assert chunk_id not in [r[0] for r in sparse_only]

        dense_only = await conn.execute(
            _DENSE_ONLY_QUERY,
            {"acl": ["grp_all_staff"], "qvec": vector_literal(BASE_VECTOR), "n": 5},
        )
        assert chunk_id in [r[0] for r in dense_only]

    async def test_missing_tenant_setting_returns_zero_rows(self, engine, tenant_id: str) -> None:
        """§28.9 row 3: RLS, not an application-level filter, is what
        makes a forgotten `set_config` return nothing instead of another
        tenant's chunks."""
        async with engine.begin() as raw_conn:
            await raw_conn.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            doc_id, chunk_id = f"doc_{uuid.uuid4().hex[:8]}", f"chk_{uuid.uuid4().hex[:8]}"
            await _insert_document(
                raw_conn,
                document_id=doc_id,
                tenant_id=tenant_id,
                source_uri="x.md",
                acl=["grp_all_staff"],
            )
            await _insert_chunk(
                raw_conn,
                chunk_id=chunk_id,
                document_id=doc_id,
                tenant_id=tenant_id,
                acl=["grp_all_staff"],
                content="konten yang seharusnya tidak pernah terlihat tanpa tenant context",
                embedding=BASE_VECTOR,
            )

        async with engine.begin() as no_tenant_conn:
            rows = await hybrid_search(
                no_tenant_conn,
                query_vector=BASE_VECTOR,
                query_text="konten seharusnya tidak terlihat",
                acl_group_ids=["grp_all_staff"],
                n_candidates=50,
                n_out=20,
            )
        assert rows == []

    async def test_relevant_document_found_among_5000_other_chunks(
        self, conn: AsyncConnection, tenant_id: str
    ) -> None:
        """§28.9 row 4 — §28.4's filtered-recall hazard: a selective ACL
        filter can make HNSW's approximate search return only candidates
        that get filtered away entirely, silently tanking recall. 5,000
        ACL-mismatched "noise" chunks, embedded close enough to compete
        for HNSW's candidate slots, must not bury the one chunk that
        actually matches the user's ACL."""
        doc_id, target_chunk_id = (
            f"doc_{uuid.uuid4().hex[:8]}",
            f"chk_target_{uuid.uuid4().hex[:8]}",
        )
        await _insert_document(
            conn, document_id=doc_id, tenant_id=tenant_id, source_uri="target.md", acl=["grp_hr"]
        )
        await _insert_chunk(
            conn,
            chunk_id=target_chunk_id,
            document_id=doc_id,
            tenant_id=tenant_id,
            acl=["grp_hr"],
            content="informasi payroll yang relevan dan harus tetap ditemukan",
            embedding=_close_vector(seed=2),
        )

        noise_doc_id = f"doc_noise_{uuid.uuid4().hex[:8]}"
        await _insert_document(
            conn,
            document_id=noise_doc_id,
            tenant_id=tenant_id,
            source_uri="noise.md",
            acl=["grp_other"],
        )
        noise_rows = [
            {
                "id": f"chk_noise_{i}_{uuid.uuid4().hex[:6]}",
                "document_id": noise_doc_id,
                "tenant_id": tenant_id,
                "acl": ["grp_other"],
                "content": f"dokumen noise nomor {i} tidak relevan",
                "embedding": vector_literal(_noise_vector(seed=i)),
                "content_hash": f"noise-{i}",
            }
            for i in range(5000)
        ]
        await conn.execute(
            text(
                "INSERT INTO catalog.chunks "
                "(id, document_id, tenant_id, acl_group_ids, content, embedding, source_uri, "
                "content_hash) "
                "VALUES (:id, :document_id, :tenant_id, :acl, :content, "
                "CAST(:embedding AS vector), "
                "'file://noise', :content_hash)"
            ),
            noise_rows,
        )

        rows = await hybrid_search(
            conn,
            query_vector=BASE_VECTOR,
            query_text="informasi payroll relevan",
            acl_group_ids=["grp_hr"],
            n_candidates=50,
            n_out=20,
        )
        assert target_chunk_id in [r.chunk_id for r in rows]

    async def test_user_without_hr_acl_gets_zero_results_from_hr_document(
        self, conn: AsyncConnection, tenant_id: str
    ) -> None:
        """§28.9 row 5 — row-level ACL, not tenant isolation: a user missing
        `grp_hr` must get zero results from an `grp_hr`-only document, even
        within their own tenant."""
        doc_id, chunk_id = f"doc_{uuid.uuid4().hex[:8]}", f"chk_{uuid.uuid4().hex[:8]}"
        await _insert_document(
            conn,
            document_id=doc_id,
            tenant_id=tenant_id,
            source_uri="sop-payroll.md",
            acl=["grp_hr"],
        )
        await _insert_chunk(
            conn,
            chunk_id=chunk_id,
            document_id=doc_id,
            tenant_id=tenant_id,
            acl=["grp_hr"],
            content="SOP penyesuaian payroll rahasia, hanya untuk HR",
            embedding=BASE_VECTOR,
        )

        rows = await hybrid_search(
            conn,
            query_vector=BASE_VECTOR,
            query_text="SOP penyesuaian payroll",
            acl_group_ids=["grp_all_staff"],  # no grp_hr
            n_candidates=50,
            n_out=20,
        )
        assert chunk_id not in [r.chunk_id for r in rows]
