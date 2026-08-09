"""§5.9 — gateway's half of the async pipeline: publish only. Queue/DLX/
retry topology (§5.9's exchange diagram) is declared once by async-worker
at its own startup, not here — a publisher only needs the exchange to
exist, and `declare_exchange` is idempotent (a no-op if async-worker
already declared the identical exchange first, whichever service starts
first). `aio_pika.connect_robust` reconnects automatically on a dropped
AMQP connection — RabbitMQ restarts shouldn't need a gateway restart too.
"""

from __future__ import annotations

import aio_pika
from contracts.jobs import JOBS_EXCHANGE, AsyncJobMessage, routing_key


class RabbitMQPublisher:
    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        channel = await self._connection.channel()
        self._exchange = await channel.declare_exchange(
            JOBS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )

    async def publish_job(self, message: AsyncJobMessage) -> None:
        assert self._exchange is not None, "RabbitMQPublisher.connect() was never called"
        body = message.model_dump_json().encode()
        await self._exchange.publish(
            aio_pika.Message(
                body=body,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key=routing_key(message.priority, message.tenant_id),
        )

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
