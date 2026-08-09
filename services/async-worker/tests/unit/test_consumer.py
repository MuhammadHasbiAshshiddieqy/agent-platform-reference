"""§5.9's wire-level routing decision: `make_message_handler`'s callback
must translate each `JobOutcome` into the right AMQP action — schedule a
backoff retry, land the message in the DLQ, or just ack. `process_job`
itself is faked here (its own branching is covered by test_processor.py)
so this file is purely about consumer.py's dispatch, including the
DEAD_LETTERED -> DLQ-publish path a live M6 test caught missing (the
outcome was correctly persisted to `jobs.async_jobs` and the webhook
fired, but the RabbitMQ DLQ queue itself stayed empty since nothing
published the message body there for the "retries exhausted" branch,
as opposed to the unexpected-exception/poison-pill branch which always
did).
"""

from __future__ import annotations

from typing import Any

import pytest
from _worker_helpers import make_job_message
from async_worker.consumer import make_message_handler
from async_worker.processor import JobOutcome


class _FakeExchange:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, message: Any, *, routing_key: str) -> None:
        self.published.append({"body": message.body, "routing_key": routing_key})


class _FakeChannel:
    def __init__(self) -> None:
        self.default_exchange = _FakeExchange()


class _FakeIncomingMessage:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.acked = False

    async def ack(self) -> None:
        self.acked = True


def _handler_deps() -> dict[str, Any]:
    return {
        "channel": _FakeChannel(),
        "retry_exchange": _FakeExchange(),
        "harness": object(),
        "quota": object(),
        "webhook_sender": object(),
        "model_router_key_override": "async-pool-key-test",
    }


@pytest.mark.asyncio
async def test_retry_outcome_schedules_backoff_and_does_not_touch_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_process_job(*args: Any, **kwargs: Any) -> JobOutcome:
        return JobOutcome.RETRY

    monkeypatch.setattr("async_worker.consumer.process_job", fake_process_job)
    deps = _handler_deps()
    handler = make_message_handler(**deps)
    job_message = make_job_message()
    incoming = _FakeIncomingMessage(job_message.model_dump_json().encode())

    await handler(incoming)  # type: ignore[arg-type]

    assert len(deps["retry_exchange"].published) == 1
    assert deps["channel"].default_exchange.published == []
    assert incoming.acked is True


@pytest.mark.asyncio
async def test_dead_lettered_outcome_publishes_to_dlq(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_process_job(*args: Any, **kwargs: Any) -> JobOutcome:
        return JobOutcome.DEAD_LETTERED

    monkeypatch.setattr("async_worker.consumer.process_job", fake_process_job)
    deps = _handler_deps()
    handler = make_message_handler(**deps)
    job_message = make_job_message()
    incoming = _FakeIncomingMessage(job_message.model_dump_json().encode())

    await handler(incoming)  # type: ignore[arg-type]

    assert deps["retry_exchange"].published == []
    dlq_publishes = deps["channel"].default_exchange.published
    assert len(dlq_publishes) == 1
    assert dlq_publishes[0]["routing_key"] == "agent.jobs.dlq"
    assert dlq_publishes[0]["body"] == incoming.body
    assert incoming.acked is True


@pytest.mark.asyncio
async def test_succeeded_and_already_claimed_outcomes_only_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for outcome in (JobOutcome.SUCCEEDED, JobOutcome.ALREADY_CLAIMED):

        async def fake_process_job(
            *args: Any, _outcome: JobOutcome = outcome, **kwargs: Any
        ) -> JobOutcome:
            return _outcome

        monkeypatch.setattr("async_worker.consumer.process_job", fake_process_job)
        deps = _handler_deps()
        handler = make_message_handler(**deps)
        job_message = make_job_message()
        incoming = _FakeIncomingMessage(job_message.model_dump_json().encode())

        await handler(incoming)  # type: ignore[arg-type]

        assert deps["retry_exchange"].published == []
        assert deps["channel"].default_exchange.published == []
        assert incoming.acked is True


@pytest.mark.asyncio
async def test_unparseable_message_is_routed_to_dlq_without_calling_process_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_process_job(*args: Any, **kwargs: Any) -> JobOutcome:
        nonlocal called
        called = True
        return JobOutcome.SUCCEEDED

    monkeypatch.setattr("async_worker.consumer.process_job", fake_process_job)
    deps = _handler_deps()
    handler = make_message_handler(**deps)
    incoming = _FakeIncomingMessage(b"not valid json at all")

    await handler(incoming)  # type: ignore[arg-type]

    assert called is False
    dlq_publishes = deps["channel"].default_exchange.published
    assert len(dlq_publishes) == 1
    assert dlq_publishes[0]["routing_key"] == "agent.jobs.dlq"
    assert incoming.acked is True


@pytest.mark.asyncio
async def test_unexpected_exception_in_process_job_is_routed_to_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_process_job(*args: Any, **kwargs: Any) -> JobOutcome:
        raise RuntimeError("boom")

    monkeypatch.setattr("async_worker.consumer.process_job", fake_process_job)
    deps = _handler_deps()
    handler = make_message_handler(**deps)
    job_message = make_job_message()
    incoming = _FakeIncomingMessage(job_message.model_dump_json().encode())

    await handler(incoming)  # type: ignore[arg-type]

    dlq_publishes = deps["channel"].default_exchange.published
    assert len(dlq_publishes) == 1
    assert incoming.acked is True
