"""§8.5 / §28.4 catalog schema writes. Raw SQL via SQLAlchemy Core `text()`
— consistent with migrations/ (no ORM).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(repr(v) for v in values) + "]"


async def get_document_hash(
    conn: AsyncConnection, *, tenant_id: str, source: str, source_uri: str
) -> str | None:
    """None means "not present (or soft-deleted)" — either way, the caller
    treats it as new, not as an incremental skip."""
    result = await conn.execute(
        text(
            "SELECT content_hash FROM catalog.documents "
            "WHERE tenant_id = :tenant_id AND source = :source AND source_uri = :source_uri "
            "AND deleted_at IS NULL"
        ),
        {"tenant_id": tenant_id, "source": source, "source_uri": source_uri},
    )
    row = result.first()
    return row[0] if row else None


async def upsert_document(
    conn: AsyncConnection,
    *,
    candidate_id: str,
    tenant_id: str,
    source: str,
    source_uri: str,
    title: str | None,
    content_hash: str,
    acl_group_ids: list[str],
    lang: str | None,
) -> str:
    """Returns the document's actual id — on conflict, the existing row's
    id wins (never the freshly generated `candidate_id`); RETURNING is
    what makes that visible instead of assumed."""
    result = await conn.execute(
        text(
            "INSERT INTO catalog.documents "
            "(id, tenant_id, source, source_uri, title, content_hash, acl_group_ids, lang, "
            " deleted_at) "
            "VALUES (:id, :tenant_id, :source, :source_uri, :title, :content_hash, "
            ":acl_group_ids, :lang, NULL) "
            "ON CONFLICT (tenant_id, source, source_uri) DO UPDATE SET "
            "title = EXCLUDED.title, content_hash = EXCLUDED.content_hash, "
            "acl_group_ids = EXCLUDED.acl_group_ids, lang = EXCLUDED.lang, deleted_at = NULL "
            "RETURNING id"
        ),
        {
            "id": candidate_id,
            "tenant_id": tenant_id,
            "source": source,
            "source_uri": source_uri,
            "title": title,
            "content_hash": content_hash,
            "acl_group_ids": acl_group_ids,
            "lang": lang,
        },
    )
    row = result.first()
    assert row is not None
    return str(row[0])


async def delete_chunks_for_document(conn: AsyncConnection, document_id: str) -> None:
    await conn.execute(
        text("DELETE FROM catalog.chunks WHERE document_id = :document_id"),
        {"document_id": document_id},
    )


async def insert_chunk(
    conn: AsyncConnection,
    *,
    chunk_id: str,
    document_id: str,
    tenant_id: str,
    acl_group_ids: list[str],
    content: str,
    embedding: list[float],
    section_path: str | None,
    source_uri: str,
    lang: str | None,
    content_hash: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO catalog.chunks "
            "(id, document_id, tenant_id, acl_group_ids, content, embedding, section_path, "
            " source_uri, lang, content_hash) "
            "VALUES (:id, :document_id, :tenant_id, :acl_group_ids, :content, "
            " CAST(:embedding AS vector), :section_path, :source_uri, :lang, :content_hash)"
        ),
        {
            "id": chunk_id,
            "document_id": document_id,
            "tenant_id": tenant_id,
            "acl_group_ids": acl_group_ids,
            "content": content,
            "embedding": vector_literal(embedding),
            "section_path": section_path,
            "source_uri": source_uri,
            "lang": lang,
            "content_hash": content_hash,
        },
    )


async def tombstone_missing_documents(
    conn: AsyncConnection, *, tenant_id: str, source: str, seen_source_uris: set[str]
) -> list[str]:
    """§11.1 — a document that vanished from the source is soft-deleted
    (`deleted_at`) and its chunks hard-deleted; re-ingesting a since-removed
    source_uri clears the tombstone (upsert_document sets `deleted_at =
    NULL`). Returns the tombstoned document_ids — §10's cache invalidation
    treats a delete as a document change too, same as a content edit."""
    result = await conn.execute(
        text(
            "SELECT id, source_uri FROM catalog.documents "
            "WHERE tenant_id = :tenant_id AND source = :source AND deleted_at IS NULL"
        ),
        {"tenant_id": tenant_id, "source": source},
    )
    rows = result.fetchall()
    tombstoned: list[str] = []
    for document_id, source_uri in rows:
        if source_uri in seen_source_uris:
            continue
        await conn.execute(
            text("UPDATE catalog.documents SET deleted_at = now() WHERE id = :id"),
            {"id": document_id},
        )
        await delete_chunks_for_document(conn, document_id)
        tombstoned.append(str(document_id))
    return tombstoned


async def insert_ingestion_run(
    conn: AsyncConnection,
    *,
    run_id: str,
    source: str,
    tenant_id: str,
    status: str,
    docs_seen: int,
    docs_upserted: int,
    docs_deleted: int,
    errors: int,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO catalog.ingestion_runs "
            "(id, source, tenant_id, status, docs_seen, docs_upserted, docs_deleted, errors, "
            " finished_at) "
            "VALUES (:id, :source, :tenant_id, :status, :docs_seen, :docs_upserted, "
            " :docs_deleted, :errors, now())"
        ),
        {
            "id": run_id,
            "source": source,
            "tenant_id": tenant_id,
            "status": status,
            "docs_seen": docs_seen,
            "docs_upserted": docs_upserted,
            "docs_deleted": docs_deleted,
            "errors": errors,
        },
    )


async def insert_ingestion_error(
    conn: AsyncConnection,
    *,
    error_id: str,
    run_id: str,
    tenant_id: str,
    source_uri: str,
    error_message: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO catalog.ingestion_errors "
            "(id, ingestion_run_id, tenant_id, source_uri, error_message) "
            "VALUES (:id, :run_id, :tenant_id, :source_uri, :error_message)"
        ),
        {
            "id": error_id,
            "run_id": run_id,
            "tenant_id": tenant_id,
            "source_uri": source_uri,
            "error_message": error_message,
        },
    )
