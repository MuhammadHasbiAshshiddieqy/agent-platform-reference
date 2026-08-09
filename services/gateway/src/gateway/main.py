from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from gateway.api.routes import router
from gateway.clients.harness import HarnessClient
from gateway.clients.rabbitmq import RabbitMQPublisher
from gateway.config import settings
from gateway.quota import QuotaManager
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis

logger = logging.getLogger("gateway.sweeper")


async def _sweep_loop(quota: QuotaManager, interval_seconds: float) -> None:
    """§23.2a — reclaims reservations no request path ever reconciled
    (crashed instance, killed request). Runs for the life of the process;
    a swept count > 0 most ticks would mean instances are dying mid-run
    often enough to investigate."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            swept = await quota.sweep_once()
            if swept:
                logger.info("quota sweeper reclaimed %d expired reservation(s)", swept)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("quota sweeper pass failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.harness = HarnessClient(settings.harness_url, settings.sync_timeout_seconds)
    app.state.sync_timeout_seconds = settings.sync_timeout_seconds

    redis = Redis.from_url(settings.redis_url)
    quota = QuotaManager(
        redis,
        sync_limit=settings.sync_tokens_per_hour,
        async_limit=settings.async_tokens_per_day,
    )
    app.state.quota = quota
    sweeper_task = asyncio.create_task(_sweep_loop(quota, settings.quota_sweep_interval_seconds))

    rabbitmq = RabbitMQPublisher(settings.rabbitmq_url)
    await rabbitmq.connect()
    app.state.rabbitmq = rabbitmq
    app.state.async_job_deadline_seconds = settings.async_job_deadline_seconds

    yield

    sweeper_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweeper_task
    await redis.aclose()
    await app.state.harness.aclose()
    await rabbitmq.close()


app = FastAPI(title="agent-gateway", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
