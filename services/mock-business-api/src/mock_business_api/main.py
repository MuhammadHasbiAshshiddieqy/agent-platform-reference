from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mock_business_api.api import hr, payroll
from mock_business_api.config import settings
from mock_business_api.idempotency import IdempotencyStore
from mock_business_api.preview_tokens import PreviewTokenStore
from mock_business_api.state import BusinessState


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.business_state = BusinessState(settings.business_state_path, settings.users_path)
    app.state.preview_tokens = PreviewTokenStore(settings.preview_token_ttl_seconds)
    app.state.idempotency = IdempotencyStore()
    yield


app = FastAPI(title="mock-business-api", lifespan=lifespan)
app.include_router(hr.router)
app.include_router(payroll.router)


@app.post("/internal/v1/reset")
async def reset() -> dict[str, str]:
    """`make reset` (§27.2) — returns demo state to seed values without a
    container restart. Preview tokens and execute idempotency records are
    intentionally NOT cleared: they're either already consumed/expired or
    still valid, and clearing them mid-demo would let a replayed execute
    double-spend a leave balance that reset just restored."""
    app.state.business_state.reset()
    return {"status": "reset"}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}
