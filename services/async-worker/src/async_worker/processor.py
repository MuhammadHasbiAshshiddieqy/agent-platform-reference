"""§5.10's job executor core. One call = one job attempt: claim (§23.2f),
call harness, record the result, reconcile quota, send the webhook. The
retry-vs-DLQ *decision* (comparing `attempts` against `MAX_ATTEMPTS`,
republishing with backoff) lives in `consumer.py`, which owns the AMQP
channel this module deliberately doesn't touch — `process_job` reports
what happened via `JobOutcome`, nothing more.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum

from async_worker.clients.harness import HarnessClient, HarnessError
from async_worker.clients.quota import QuotaReconciler
from async_worker.persistence.db import tenant_session
from async_worker.persistence.jobs import (
    mark_job_dead_lettered,
    mark_job_failed_will_retry,
    mark_job_succeeded,
    try_claim_job,
    update_job_callback_status,
)
from async_worker.webhook import WebhookSender
from contracts.gateway import JobError
from contracts.jobs import MAX_ATTEMPTS, AsyncJobMessage, WebhookPayload
from prometheus_client import Counter

logger = logging.getLogger("async_worker.processor")

async_jobs_dead_lettered_total = Counter(
    "async_jobs_dead_lettered_total",
    "§5.9's DoD — every job that exhausted retries or hit a non-retriable error",
    ["tenant_id"],
)


class JobOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    RETRY = "retry"
    DEAD_LETTERED = "dead_lettered"
    ALREADY_CLAIMED = "already_claimed"


async def _send_terminal_webhook(
    webhook_sender: WebhookSender,
    message: AsyncJobMessage,
    *,
    status: str,
    result: object = None,
    error: JobError | None = None,
) -> None:
    if message.callback_url is None:
        return
    payload = WebhookPayload(
        job_id=message.job_id,
        trace_id=message.run_request.trace_id,
        status=status,  # type: ignore[arg-type]
        result=result,  # type: ignore[arg-type]
        error=error,
        completed_at=datetime.now(UTC),
    )
    delivered = await webhook_sender.send(
        url=message.callback_url, secret_ref=message.callback_secret_ref, payload=payload
    )
    async with tenant_session(message.tenant_id) as conn:
        await update_job_callback_status(
            conn,
            tenant_id=message.tenant_id,
            job_id=message.job_id,
            callback_status="delivered" if delivered else "webhook_failed",
        )


async def process_job(
    message: AsyncJobMessage,
    *,
    harness: HarnessClient,
    quota: QuotaReconciler,
    webhook_sender: WebhookSender,
    model_router_key_override: str,
) -> JobOutcome:
    async with tenant_session(message.tenant_id) as conn:
        claimed = await try_claim_job(conn, tenant_id=message.tenant_id, job_id=message.job_id)
    if not claimed:
        # §23.2f — redelivery of an already-claimed (or already-terminal)
        # job. Not an error: at-least-once delivery means this is
        # expected, not exceptional.
        logger.info("job %s already claimed, skipping redelivery", message.job_id)
        return JobOutcome.ALREADY_CLAIMED

    # §6 L3 — gateway built `run_request` without knowing this key (it
    # doesn't provision it); the worker sets it on its own copy right
    # before the one call that actually spends budget.
    run_request = message.run_request.model_copy(
        update={"model_router_key_override": model_router_key_override}
    )

    try:
        result = await harness.run(run_request)
    except HarnessError as exc:
        error = JobError(
            code=f"HTTP_{exc.status_code}", message=exc.detail, retriable=exc.retriable
        )
        async with tenant_session(message.tenant_id) as conn:
            await mark_job_failed_will_retry(
                conn, tenant_id=message.tenant_id, job_id=message.job_id, error=error.model_dump()
            )

        # A non-retriable error (4xx — bad input, guardrail refusal) will
        # fail identically on every retry; don't spend the backoff budget
        # proving that three more times. `message.attempts` is the count
        # of retries *already scheduled* (0 on the very first attempt),
        # so `>= MAX_ATTEMPTS` means this failure is attempt #4 — the
        # backoff schedule (§5.9: 10s/60s/300s, 3 entries) is exhausted.
        if not exc.retriable or message.attempts >= MAX_ATTEMPTS:
            async with tenant_session(message.tenant_id) as conn:
                await mark_job_dead_lettered(
                    conn,
                    tenant_id=message.tenant_id,
                    job_id=message.job_id,
                    error=error.model_dump(),
                )
            async_jobs_dead_lettered_total.labels(tenant_id=message.tenant_id).inc()
            logger.error("job %s dead-lettered: %s", message.job_id, error.message)
            await _send_terminal_webhook(
                webhook_sender, message, status="dead_lettered", error=error
            )
            return JobOutcome.DEAD_LETTERED
        return JobOutcome.RETRY

    if message.quota_reservation_key:
        actual_tokens = result.usage.input_tokens + result.usage.output_tokens
        await quota.reconcile(message.quota_reservation_key, actual_tokens)

    async with tenant_session(message.tenant_id) as conn:
        await mark_job_succeeded(
            conn,
            tenant_id=message.tenant_id,
            job_id=message.job_id,
            result=result.model_dump(mode="json"),
        )

    await _send_terminal_webhook(webhook_sender, message, status="succeeded", result=result)
    return JobOutcome.SUCCEEDED
