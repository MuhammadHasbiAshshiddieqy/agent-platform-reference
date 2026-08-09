"""§5.9's AMQP consumer loop. `process_job` decides WHAT happened
(`JobOutcome`); this module decides what to DO about it on the wire —
schedule a backoff retry, publish to the DLQ, or just ack a success.
Every code path ends in exactly one `message.ack()` — RabbitMQ is
at-least-once, so a message this consumer never acks gets redelivered
(to this or another worker instance) once the connection's visibility
window elapses, and `process_job`'s own `try_claim_job` handles that
redelivery safely (§23.2f) — but retry/DLQ routing is explicit here,
never left to an implicit nack-requeue with no backoff.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import aio_pika
from async_worker.clients.harness import HarnessClient
from async_worker.clients.quota import QuotaReconciler
from async_worker.processor import JobOutcome, process_job
from async_worker.webhook import WebhookSender
from contracts.jobs import (
    DLQ_QUEUE,
    MAX_ATTEMPTS,
    RETRY_BACKOFF_SECONDS,
    AsyncJobMessage,
    routing_key,
)

logger = logging.getLogger("async_worker.consumer")


async def _schedule_retry(
    retry_exchange: aio_pika.abc.AbstractExchange, message: AsyncJobMessage
) -> None:
    delay_seconds = RETRY_BACKOFF_SECONDS[min(message.attempts, MAX_ATTEMPTS - 1)]
    retried = message.model_copy(update={"attempts": message.attempts + 1})
    body = retried.model_dump_json().encode()
    await retry_exchange.publish(
        aio_pika.Message(
            body=body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            expiration=delay_seconds,
        ),
        # The retry exchange is a fanout — this routing key isn't used
        # for THIS hop, but RabbitMQ stores it on the message and reuses
        # it when the holding queue's TTL later dead-letters back through
        # JOBS_EXCHANGE (a topic exchange, where it matters).
        routing_key=routing_key(message.priority, message.tenant_id),
    )
    logger.info(
        "job %s scheduled for retry in %ds (attempt %d)",
        message.job_id,
        delay_seconds,
        retried.attempts,
    )


async def _publish_to_dlq(channel: aio_pika.abc.AbstractChannel, message: AsyncJobMessage) -> None:
    await channel.default_exchange.publish(
        aio_pika.Message(
            body=message.model_dump_json().encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        ),
        routing_key=DLQ_QUEUE,
    )


async def _publish_raw_to_dlq(channel: aio_pika.abc.AbstractChannel, body: bytes) -> None:
    """Poison-pill path — the message didn't even parse as a valid
    `AsyncJobMessage`, or something in `process_job` itself raised
    unexpectedly. There's no job_id to update `jobs.async_jobs` against
    with any confidence, so this is DLQ-only, for operator inspection —
    not the same code path as `process_job`'s own explicit dead-letter
    branch, which always has a real job to update."""
    await channel.default_exchange.publish(
        aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
        routing_key=DLQ_QUEUE,
    )


def make_message_handler(
    *,
    channel: aio_pika.abc.AbstractChannel,
    retry_exchange: aio_pika.abc.AbstractExchange,
    harness: HarnessClient,
    quota: QuotaReconciler,
    webhook_sender: WebhookSender,
    model_router_key_override: str,
) -> Callable[[aio_pika.abc.AbstractIncomingMessage], Awaitable[None]]:
    async def _handle(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            job_message = AsyncJobMessage.model_validate_json(message.body)
        except Exception:
            logger.exception("unparseable job message, routing to DLQ")
            await _publish_raw_to_dlq(channel, message.body)
            await message.ack()
            return

        try:
            outcome = await process_job(
                job_message,
                harness=harness,
                quota=quota,
                webhook_sender=webhook_sender,
                model_router_key_override=model_router_key_override,
            )
        except Exception:
            logger.exception("unexpected error processing job %s", job_message.job_id)
            await _publish_to_dlq(channel, job_message)
            await message.ack()
            return

        if outcome == JobOutcome.RETRY:
            await _schedule_retry(retry_exchange, job_message)
        elif outcome == JobOutcome.DEAD_LETTERED:
            # `process_job` already recorded the terminal DB status and
            # sent the webhook — this is the RabbitMQ-side twin of that:
            # land the message body in the DLQ so an operator has
            # something to inspect/replay, same as the poison-pill path
            # above.
            await _publish_to_dlq(channel, job_message)

        await message.ack()

    return _handle
