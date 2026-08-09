from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from ingestion.clients.harness import HarnessCacheClient
from ingestion.clients.model_router import EmbeddingClient
from ingestion.config import settings
from ingestion.pipeline import run_filesystem_ingestion


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.embedder = EmbeddingClient(
        settings.model_router_url, settings.model_router_key, settings.embedding_model_alias
    )
    app.state.harness_cache = HarnessCacheClient(
        settings.harness_url, settings.harness_timeout_seconds
    )
    yield
    await app.state.embedder.aclose()
    await app.state.harness_cache.aclose()


app = FastAPI(title="ingestion-service", lifespan=lifespan)


@app.post("/internal/v1/ingest/{tenant_id}")
async def trigger_ingest(tenant_id: str) -> dict[str, Any]:
    """§5.11 — "trigger manual & health" (Prefect/cron drive this on a
    schedule at production scale; M3 exposes the same operation as a
    plain POST, which is also exactly what tests and `make ingest` call)."""
    embedder: EmbeddingClient = app.state.embedder
    harness_cache: HarnessCacheClient = app.state.harness_cache
    return await run_filesystem_ingestion(tenant_id, embedder, harness_cache)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}
