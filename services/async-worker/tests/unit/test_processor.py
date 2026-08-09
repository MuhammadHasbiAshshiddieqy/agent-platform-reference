"""§5.9/§23.2f's job-processing decision logic: claim (already covered by
`try_claim_job`'s own SQL, exercised for real in `tests/integration/
test_m6_async.py`), retry-vs-DLQ branching, and webhook triggering.
Persistence calls are monkeypatched to fakes here (not a real Postgres,
unlike `services/retrieval/tests`'s testcontainers pattern) — this file
is about `process_job`'s own branching, not proving the SQL again.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from _worker_helpers import (
    FakeHarnessClient,
    FakeQuotaReconciler,
    FakeWebhookSender,
    make_job_message,
)
from async_worker.clients.harness import HarnessError
from async_worker.processor import JobOutcome, process_job


@asynccontextmanager
async def _fake_tenant_session(tenant_id: str):
    yield None


class _RecordingPersistence:
    def __init__(self, *, claim_succeeds: bool = True) -> None:
        self._claim_succeeds = claim_succeeds
        self.succeeded: list[dict[str, Any]] = []
        self.failed_will_retry: list[dict[str, Any]] = []
        self.dead_lettered: list[dict[str, Any]] = []
        self.callback_statuses: list[dict[str, Any]] = []

    async def try_claim_job(self, conn: object, *, tenant_id: str, job_id: str) -> bool:
        return self._claim_succeeds

    async def mark_job_succeeded(
        self, conn: object, *, tenant_id: str, job_id: str, result: dict
    ) -> None:
        self.succeeded.append({"tenant_id": tenant_id, "job_id": job_id, "result": result})

    async def mark_job_failed_will_retry(
        self, conn: object, *, tenant_id: str, job_id: str, error: dict
    ) -> None:
        self.failed_will_retry.append({"tenant_id": tenant_id, "job_id": job_id, "error": error})

    async def mark_job_dead_lettered(
        self, conn: object, *, tenant_id: str, job_id: str, error: dict
    ) -> None:
        self.dead_lettered.append({"tenant_id": tenant_id, "job_id": job_id, "error": error})

    async def update_job_callback_status(
        self, conn: object, *, tenant_id: str, job_id: str, callback_status: str
    ) -> None:
        self.callback_statuses.append(
            {"tenant_id": tenant_id, "job_id": job_id, "callback_status": callback_status}
        )


def _patch_persistence(monkeypatch: pytest.MonkeyPatch, persistence: _RecordingPersistence) -> None:
    monkeypatch.setattr("async_worker.processor.tenant_session", _fake_tenant_session)
    monkeypatch.setattr("async_worker.processor.try_claim_job", persistence.try_claim_job)
    monkeypatch.setattr("async_worker.processor.mark_job_succeeded", persistence.mark_job_succeeded)
    monkeypatch.setattr(
        "async_worker.processor.mark_job_failed_will_retry", persistence.mark_job_failed_will_retry
    )
    monkeypatch.setattr(
        "async_worker.processor.mark_job_dead_lettered", persistence.mark_job_dead_lettered
    )
    monkeypatch.setattr(
        "async_worker.processor.update_job_callback_status", persistence.update_job_callback_status
    )


@pytest.mark.asyncio
async def test_already_claimed_job_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    persistence = _RecordingPersistence(claim_succeeds=False)
    _patch_persistence(monkeypatch, persistence)
    harness = FakeHarnessClient()

    outcome = await process_job(
        make_job_message(),
        harness=harness,  # type: ignore[arg-type]
        quota=FakeQuotaReconciler(),  # type: ignore[arg-type]
        webhook_sender=FakeWebhookSender(),  # type: ignore[arg-type]
        model_router_key_override="async-pool-key-test",
    )

    assert outcome == JobOutcome.ALREADY_CLAIMED
    assert harness.calls == []  # never even tried to call harness


@pytest.mark.asyncio
async def test_successful_run_marks_succeeded_reconciles_quota_and_sends_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = _RecordingPersistence()
    _patch_persistence(monkeypatch, persistence)
    harness = FakeHarnessClient()
    quota = FakeQuotaReconciler()
    webhook = FakeWebhookSender()

    outcome = await process_job(
        make_job_message(),
        harness=harness,  # type: ignore[arg-type]
        quota=quota,  # type: ignore[arg-type]
        webhook_sender=webhook,  # type: ignore[arg-type]
        model_router_key_override="async-pool-key-test",
    )

    assert outcome == JobOutcome.SUCCEEDED
    assert len(persistence.succeeded) == 1
    assert quota.calls == [("quota:reservation:tnt_demo:async:job_test", 150)]
    assert len(webhook.calls) == 1
    assert persistence.callback_statuses[0]["callback_status"] == "delivered"
    # §6 L3 — the async-pool key actually reached the harness call.
    assert harness.calls[0].model_router_key_override == "async-pool-key-test"


@pytest.mark.asyncio
async def test_retriable_failure_below_max_attempts_is_marked_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = _RecordingPersistence()
    _patch_persistence(monkeypatch, persistence)
    harness = FakeHarnessClient(error=HarnessError(503, "harness unavailable", retriable=True))

    outcome = await process_job(
        make_job_message(attempts=0),
        harness=harness,  # type: ignore[arg-type]
        quota=FakeQuotaReconciler(),  # type: ignore[arg-type]
        webhook_sender=FakeWebhookSender(),  # type: ignore[arg-type]
        model_router_key_override="async-pool-key-test",
    )

    assert outcome == JobOutcome.RETRY
    assert len(persistence.failed_will_retry) == 1
    assert persistence.dead_lettered == []


@pytest.mark.asyncio
async def test_retriable_failure_at_max_attempts_is_dead_lettered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = _RecordingPersistence()
    _patch_persistence(monkeypatch, persistence)
    # attempts=3 means this is the 4th total call — §5.9's backoff
    # schedule (10s/60s/300s, 3 entries) is exhausted.
    harness = FakeHarnessClient(error=HarnessError(503, "harness unavailable", retriable=True))
    webhook = FakeWebhookSender()

    outcome = await process_job(
        make_job_message(attempts=3),
        harness=harness,  # type: ignore[arg-type]
        quota=FakeQuotaReconciler(),  # type: ignore[arg-type]
        webhook_sender=webhook,  # type: ignore[arg-type]
        model_router_key_override="async-pool-key-test",
    )

    assert outcome == JobOutcome.DEAD_LETTERED
    assert len(persistence.dead_lettered) == 1
    assert len(webhook.calls) == 1  # terminal webhook sent even on dead-letter


@pytest.mark.asyncio
async def test_non_retriable_failure_is_dead_lettered_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = _RecordingPersistence()
    _patch_persistence(monkeypatch, persistence)
    # A 4xx from harness (bad input, guardrail refusal) will fail
    # identically on every retry — don't spend the backoff budget on it.
    harness = FakeHarnessClient(error=HarnessError(400, "bad request", retriable=False))

    outcome = await process_job(
        make_job_message(attempts=0),
        harness=harness,  # type: ignore[arg-type]
        quota=FakeQuotaReconciler(),  # type: ignore[arg-type]
        webhook_sender=FakeWebhookSender(),  # type: ignore[arg-type]
        model_router_key_override="async-pool-key-test",
    )

    assert outcome == JobOutcome.DEAD_LETTERED
    assert len(persistence.dead_lettered) == 1
