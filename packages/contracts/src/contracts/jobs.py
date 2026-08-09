"""§5.9/§5.10 — the async job pipeline's internal wire shapes: what
`agent-gateway` publishes to RabbitMQ and `async-worker` consumes, plus
the webhook payload `async-worker` sends to a tenant's `callback_url`.
Not part of §8.1's public API (that's `contracts.gateway`) — these never
cross the Kong boundary.

Exchange/queue naming lives here too, not duplicated in each service,
for the same reason `contracts.common.Headers` centralizes HTTP header
names: gateway (publisher) and async-worker (consumer, topology owner)
must agree on these strings without importing each other (§4.1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from contracts.common import JobPriority, StrictModel
from contracts.gateway import AgentInvokeResponse, JobError
from contracts.harness import AgentRunRequest

# §5.9's topology. One topic exchange; `job.{priority}.{tenant_id}` lets a
# future per-tenant binding exist without a topology change — today both
# queues just bind on `job.<priority>.*`.
JOBS_EXCHANGE = "agent.jobs"
STANDARD_QUEUE = "agent.jobs.standard"
BULK_QUEUE = "agent.jobs.bulk"
# Holds a nacked message until ITS OWN per-message TTL (set by the
# republisher, not the queue) expires, then dead-letters back to
# JOBS_EXCHANGE using the message's ORIGINAL routing key — RabbitMQ's
# default DLX behavior preserves it, so one holding queue serves every
# backoff tier (10s/60s/300s) without three separate TTL queues.
RETRY_EXCHANGE = "agent.jobs.retry"
RETRY_QUEUE = "agent.jobs.retry.holding"
DLQ_QUEUE = "agent.jobs.dlq"
DLQ_MESSAGE_TTL_MS = 7 * 24 * 60 * 60 * 1000  # §5.9 — 7 hari

MAX_ATTEMPTS = 3
# §5.9's literal backoff schedule, indexed by (attempts already made - 1).
RETRY_BACKOFF_SECONDS = [10, 60, 300]


def routing_key(priority: JobPriority, tenant_id: str) -> str:
    return f"job.{priority.value}.{tenant_id}"


class AsyncJobMessage(StrictModel):
    """Published to `JOBS_EXCHANGE`, consumed by async-worker. `job_id`
    is always equal to `run_request.run_id` — a job maps 1:1 to one
    harness run, so there's no reason for the two ids to diverge."""

    job_id: str
    tenant_id: str
    priority: JobPriority
    run_request: AgentRunRequest
    callback_url: str | None = None
    callback_secret_ref: str | None = None
    attempts: int = Field(default=0, ge=0)
    # §6 L2 — gateway already reserved estimated tokens against the async
    # pool before publishing; the worker reconciles the actual usage
    # against this SAME reservation once harness returns (own copy of the
    # Redis reconcile script, gateway's `QuotaManager` isn't importable
    # across the service boundary, §4.1). `None` only if the reservation
    # was somehow skipped — reconciliation is then just not attempted.
    quota_reservation_key: str | None = None


class WebhookPayload(StrictModel):
    """§5.10/DoD — signed with HMAC-SHA256 over this body's exact JSON
    bytes (`model_dump_json()`), key resolved from `callback_secret_ref`.
    Header `X-Duta-Signature: sha256=<hex>` (see `async-worker/webhook.py`).
    """

    job_id: str
    trace_id: str
    status: Literal["succeeded", "failed", "dead_lettered"]
    result: AgentInvokeResponse | None = None
    error: JobError | None = None
    completed_at: datetime
