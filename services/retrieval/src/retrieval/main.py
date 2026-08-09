from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from contracts.retrieval import SearchRequest, SearchResult
from fastapi import FastAPI
from retrieval.clients.infinity import RerankClient
from retrieval.clients.model_router import EmbeddingClient
from retrieval.config import settings
from retrieval.service import search


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.embedder = EmbeddingClient(
        settings.model_router_url, settings.model_router_key, settings.embedding_model_alias
    )
    app.state.reranker = RerankClient(settings.infinity_url, settings.rerank_model)
    yield
    await app.state.embedder.aclose()
    await app.state.reranker.aclose()


app = FastAPI(title="retrieval-service", lifespan=lifespan)


@app.post("/internal/v1/search", response_model=SearchResult)
async def internal_search(request: SearchRequest) -> SearchResult:
    return await search(
        request,
        embedder=app.state.embedder,
        reranker=app.state.reranker,
        dense_candidates=settings.dense_candidates,
        sparse_candidates=settings.sparse_candidates,
        fused_top_n=settings.fused_top_n,
        final_top_k=settings.final_top_k,
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}
