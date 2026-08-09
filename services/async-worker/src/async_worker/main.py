from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
import yaml
from async_worker.clients.harness import HarnessClient
from async_worker.clients.quota import QuotaReconciler
from async_worker.config import settings
from async_worker.consumer import make_message_handler
from async_worker.litellm_key import provision_async_pool_key
from async_worker.topology import declare_topology
from async_worker.webhook import WebhookSender
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("async_worker.main")


def _load_webhook_secrets() -> dict[str, str]:
    if not settings.webhook_secrets_path.exists():
        logger.warning(
            "no webhook secrets file at %s — outgoing webhooks will be unsigned",
            settings.webhook_secrets_path,
        )
        return {}
    data = yaml.safe_load(settings.webhook_secrets_path.read_text()) or {}
    return {str(k): str(v) for k, v in data.items()}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async_pool_key = await provision_async_pool_key(
        model_router_url=settings.model_router_url,
        master_key=settings.model_router_master_key,
        key_alias=settings.async_pool_key_alias,
        daily_budget_usd=settings.async_pool_daily_budget_usd,
        persist_path=settings.async_pool_key_path,
    )
    harness = HarnessClient(settings.harness_url, settings.harness_timeout_seconds)

    redis = Redis.from_url(settings.quota_redis_url)
    quota = QuotaReconciler(redis)

    webhook_sender = WebhookSender(
        secrets=_load_webhook_secrets(), timeout_seconds=settings.webhook_timeout_seconds
    )

    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    standard_queue, bulk_queue = await declare_topology(channel)

    retry_channel = await connection.channel()
    retry_exchange = await retry_channel.get_exchange("agent.jobs.retry")

    handler = make_message_handler(
        channel=channel,
        retry_exchange=retry_exchange,
        harness=harness,
        quota=quota,
        webhook_sender=webhook_sender,
        model_router_key_override=async_pool_key,
    )

    standard_channel = await connection.channel()
    await standard_channel.set_qos(prefetch_count=settings.standard_prefetch)
    standard_queue = await standard_channel.get_queue(standard_queue.name)
    standard_consumer_tag = await standard_queue.consume(handler)

    bulk_channel = await connection.channel()
    await bulk_channel.set_qos(prefetch_count=settings.bulk_prefetch)
    bulk_queue = await bulk_channel.get_queue(bulk_queue.name)
    bulk_consumer_tag = await bulk_queue.consume(handler)

    app.state.connection = connection

    yield

    await standard_queue.cancel(standard_consumer_tag)
    await bulk_queue.cancel(bulk_consumer_tag)
    await harness.aclose()
    await webhook_sender.aclose()
    await redis.aclose()
    await connection.close()


app = FastAPI(title="async-worker", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
