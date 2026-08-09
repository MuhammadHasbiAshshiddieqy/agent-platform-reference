"""§11.2 orchestration: embed -> hybrid search (dense+sparse, RRF) -> rerank
-> dedup -> assemble context window. §5.5's "bukan tanggung jawab" list
rules out calling an LLM to answer here — this only ever returns chunks.
"""

from __future__ import annotations

import time

from contracts.retrieval import RetrievedChunk, SearchRequest, SearchResult
from retrieval.clients.infinity import RerankClient
from retrieval.clients.model_router import EmbeddingClient
from retrieval.persistence.db import tenant_session
from retrieval.persistence.search import HybridSearchRow, hybrid_search


def _estimate_tokens(text: str) -> int:
    """Same ~4 chars/token heuristic as gateway's quota estimate
    (services/gateway/src/gateway/quota.py) — one convention, not
    reinvented per service."""
    return max(1, len(text) // 4)


def _dedup_by_content(rows: list[HybridSearchRow]) -> list[HybridSearchRow]:
    seen: set[str] = set()
    deduped = []
    for row in rows:
        if row.content in seen:
            continue
        seen.add(row.content)
        deduped.append(row)
    return deduped


def _assemble_context(
    rows: list[HybridSearchRow], max_context_tokens: int
) -> list[HybridSearchRow]:
    """Keeps the best-ranked chunks first, drops from the tail once the
    token budget runs out — never truncates a chunk's own content."""
    assembled: list[HybridSearchRow] = []
    used = 0
    for row in rows:
        cost = _estimate_tokens(row.content)
        if assembled and used + cost > max_context_tokens:
            break
        assembled.append(row)
        used += cost
    return assembled


async def search(
    request: SearchRequest,
    *,
    embedder: EmbeddingClient,
    reranker: RerankClient,
    dense_candidates: int,
    sparse_candidates: int,
    fused_top_n: int,
    final_top_k: int,
) -> SearchResult:
    started = time.perf_counter()
    degraded: list[str] = []

    query_vector = await embedder.embed_query(request.query)

    async with tenant_session(request.tenant_id) as conn:
        rows = await hybrid_search(
            conn,
            query_vector=query_vector,
            query_text=request.query,
            acl_group_ids=request.acl_group_ids,
            n_candidates=max(dense_candidates, sparse_candidates),
            n_out=fused_top_n,
        )

    rows = _dedup_by_content(rows)

    if request.rerank and rows:
        try:
            order = await reranker.rerank(request.query, [r.content for r in rows])
            rows = [rows[i] for i in order]
        except Exception:  # §5.5 failure mode: degrade, don't fail the request
            degraded.append("rerank")

    rows = rows[: request.top_k or final_top_k]
    rows = _assemble_context(rows, request.max_context_tokens)

    chunks = [
        RetrievedChunk(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            content=row.content,
            score=row.rrf_score,
            source_uri=row.source_uri,
            metadata={"section_path": row.section_path} if row.section_path else {},
        )
        for row in rows
    ]
    latency_ms = int((time.perf_counter() - started) * 1000)
    return SearchResult(chunks=chunks, degraded=degraded, latency_ms=latency_ms)
