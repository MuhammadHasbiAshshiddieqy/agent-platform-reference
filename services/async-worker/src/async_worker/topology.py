"""§5.9's queue topology, declared once at worker startup (idempotent —
`declare_exchange`/`declare_queue` are no-ops if an identical declaration
already exists, which is how gateway's publisher and this consumer can
both call `declare_exchange` without caring who runs first).

Retry-with-backoff (10s/60s/300s, §5.9) is the classic zero-plugin
RabbitMQ pattern: one holding queue (`RETRY_QUEUE`) with no consumer at
all. `processor.py` sets a PER-MESSAGE TTL (the `expiration` property,
not a queue-level `x-message-ttl`) when it republishes a retriable
failure there, so the same holding queue serves all three backoff tiers.
Once that TTL elapses, RabbitMQ dead-letters the message back through
`JOBS_EXCHANGE` — using the message's ORIGINAL routing key, which is
preserved automatically since `x-dead-letter-routing-key` is never set —
landing it back in `agent.jobs.standard` or `.bulk` for reprocessing.

Deliberately NOT using nack-based automatic dead-lettering on the main
queues for the "unexpected crash" case (a poison-pill message, a bug in
`processor.py` itself) — every failure path in this service is handled
explicitly (`processor.py`'s `except Exception` branch also routes to
`DLQ_QUEUE`, by hand, same as an exhausted-retry job), so there is
exactly one code path that ever writes to the DLQ, not two subtly
different ones (implicit queue-level DLX vs. explicit application logic)
that could disagree about job status bookkeeping.
"""

from __future__ import annotations

import aio_pika
from contracts.jobs import (
    BULK_QUEUE,
    DLQ_MESSAGE_TTL_MS,
    DLQ_QUEUE,
    JOBS_EXCHANGE,
    RETRY_EXCHANGE,
    RETRY_QUEUE,
    STANDARD_QUEUE,
)


async def declare_topology(
    channel: aio_pika.abc.AbstractChannel,
) -> tuple[aio_pika.abc.AbstractQueue, aio_pika.abc.AbstractQueue]:
    jobs_exchange = await channel.declare_exchange(
        JOBS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
    )
    retry_exchange = await channel.declare_exchange(
        RETRY_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
    )

    standard_queue = await channel.declare_queue(STANDARD_QUEUE, durable=True)
    await standard_queue.bind(jobs_exchange, routing_key="job.standard.*")

    bulk_queue = await channel.declare_queue(BULK_QUEUE, durable=True)
    await bulk_queue.bind(jobs_exchange, routing_key="job.bulk.*")

    retry_queue = await channel.declare_queue(
        RETRY_QUEUE,
        durable=True,
        arguments={"x-dead-letter-exchange": JOBS_EXCHANGE},
    )
    await retry_queue.bind(retry_exchange)

    # Not bound to any exchange — `processor.py` publishes here directly
    # via the default exchange's routing-key-equals-queue-name
    # convenience, the same way it schedules a retry, just skipping the
    # holding-queue TTL hop entirely.
    await channel.declare_queue(
        DLQ_QUEUE, durable=True, arguments={"x-message-ttl": DLQ_MESSAGE_TTL_MS}
    )

    return standard_queue, bulk_queue
