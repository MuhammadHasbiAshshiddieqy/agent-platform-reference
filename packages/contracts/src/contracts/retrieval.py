"""§8.3 — retrieval-service internal API."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from contracts.common import StrictModel


class SearchRequest(StrictModel):
    trace_id: str
    tenant_id: str
    acl_group_ids: list[str] = Field(default_factory=list)
    query: str
    top_k: int = 8
    filters: dict[str, Any] = Field(default_factory=dict)
    rerank: bool = True
    max_context_tokens: int = 4000


class RetrievedChunk(StrictModel):
    chunk_id: str
    document_id: str
    content: str
    score: float
    source_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResult(StrictModel):
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)
    latency_ms: int
