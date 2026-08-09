"""§5.11 orchestration: one document at a time, one transaction each — a
document that fails must not take its siblings down with it. A single
shared transaction for the whole run would defeat that guarantee outright:
Postgres aborts an entire transaction on its first error, so every INSERT
after the bad document — already-succeeded or not — would fail too.
"""

from __future__ import annotations

import uuid
from typing import Any

from ingestion.chunking import chunk_markdown
from ingestion.clients.harness import HarnessCacheClient
from ingestion.clients.model_router import EmbeddingClient
from ingestion.config import settings
from ingestion.connectors.filesystem import SOURCE, RawDocument, load_filesystem_documents
from ingestion.persistence.db import tenant_session
from ingestion.persistence.repository import (
    delete_chunks_for_document,
    get_document_hash,
    insert_chunk,
    insert_ingestion_error,
    insert_ingestion_run,
    tombstone_missing_documents,
    upsert_document,
)


async def _ingest_one_document(
    tenant_id: str, raw: RawDocument, embedder: EmbeddingClient
) -> str | None:
    """Returns the document_id if (re-)ingested, None if skipped —
    content_hash unchanged (§11.1's incremental rule)."""
    async with tenant_session(tenant_id) as conn:
        existing_hash = await get_document_hash(
            conn, tenant_id=tenant_id, source=SOURCE, source_uri=raw.source_uri
        )
        if existing_hash == raw.content_hash:
            return None

        document_id = await upsert_document(
            conn,
            candidate_id=f"doc_{uuid.uuid4().hex[:20]}",
            tenant_id=tenant_id,
            source=SOURCE,
            source_uri=raw.source_uri,
            title=raw.title,
            content_hash=raw.content_hash,
            acl_group_ids=raw.acl_group_ids,
            lang=raw.lang,
        )
        await delete_chunks_for_document(conn, document_id)

        chunks = chunk_markdown(
            raw.content,
            target_chars=settings.chunk_target_chars,
            overlap_chars=settings.chunk_overlap_chars,
        )
        if chunks:
            embeddings = await embedder.embed([c.content for c in chunks])
            for chunk, vector in zip(chunks, embeddings, strict=True):
                await insert_chunk(
                    conn,
                    chunk_id=f"chk_{uuid.uuid4().hex[:20]}",
                    document_id=document_id,
                    tenant_id=tenant_id,
                    acl_group_ids=raw.acl_group_ids,
                    content=chunk.content,
                    embedding=vector,
                    section_path=chunk.section_path,
                    source_uri=raw.source_uri,
                    lang=raw.lang,
                    content_hash=raw.content_hash,
                )
    return document_id


async def run_filesystem_ingestion(
    tenant_id: str, embedder: EmbeddingClient, harness_cache: HarnessCacheClient
) -> dict[str, Any]:
    run_id = f"ing_{uuid.uuid4().hex[:20]}"
    docs_seen = errors = 0
    seen_source_uris: set[str] = set()
    changed_document_ids: list[str] = []

    raw_docs = [
        d for d in load_filesystem_documents(settings.documents_dir) if d.tenant_id == tenant_id
    ]

    for raw in raw_docs:
        docs_seen += 1
        seen_source_uris.add(raw.source_uri)
        try:
            document_id = await _ingest_one_document(tenant_id, raw, embedder)
            if document_id is not None:
                changed_document_ids.append(document_id)
        except Exception as exc:  # §5.11 — one bad document must not stop the run
            errors += 1
            async with tenant_session(tenant_id) as conn:
                await insert_ingestion_error(
                    conn,
                    error_id=f"ierr_{uuid.uuid4().hex[:20]}",
                    run_id=run_id,
                    tenant_id=tenant_id,
                    source_uri=raw.source_uri,
                    error_message=str(exc),
                )

    async with tenant_session(tenant_id) as conn:
        tombstoned_document_ids = await tombstone_missing_documents(
            conn, tenant_id=tenant_id, source=SOURCE, seen_source_uris=seen_source_uris
        )
        docs_upserted = len(changed_document_ids)
        docs_deleted = len(tombstoned_document_ids)
        status = "completed" if errors == 0 else "completed_with_errors"
        await insert_ingestion_run(
            conn,
            run_id=run_id,
            source=SOURCE,
            tenant_id=tenant_id,
            status=status,
            docs_seen=docs_seen,
            docs_upserted=docs_upserted,
            docs_deleted=docs_deleted,
            errors=errors,
        )

    # §10 — a content edit AND a delete both count as "the document
    # changed" for cache-invalidation purposes; best-effort (see
    # HarnessCacheClient's own docstring), never raises into this run.
    await harness_cache.invalidate(
        tenant_id=tenant_id, document_ids=[*changed_document_ids, *tombstoned_document_ids]
    )

    return {
        "run_id": run_id,
        "status": status,
        "docs_seen": docs_seen,
        "docs_upserted": docs_upserted,
        "docs_deleted": docs_deleted,
        "errors": errors,
    }
