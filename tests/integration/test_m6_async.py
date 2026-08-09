"""§5.9/§5.10's live proof of the async path, end to end through Kong: a
real job submission lands in RabbitMQ, gets claimed by `async-worker`,
executes against the real `agent-harness`, and its terminal state (plus
a signed webhook) is observable back through the gateway. Unit-level
branching (claim/retry/DLQ decisions, webhook HMAC/backoff) is already
covered fast and deterministically by `services/async-worker/tests/
unit/` — this file is only about proving the wiring between gateway,
RabbitMQ, async-worker, and harness is real, the same division of labor
`test_m5b_rbac.py` documents for its own milestone.

CPU-contention notes from CLAUDE.md apply here too: a live async job
runs the exact same guardrail+RAG+tool-calling chain as a sync request,
just one hop further away, so poll loops use generous timeouts.
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import threading
import time
import uuid
from collections.abc import Callable, Iterator

import httpx
import psycopg
import pytest
import redis

# Generous enough to survive one full retry cycle (§5.9's 10s/60s/300s
# backoff ladder = 370s worst case) on top of actual model-inference
# time under this dev machine's documented CPU contention — a live async
# request runs the exact same guardrail+RAG+tool-calling chain a sync
# request does, just with an extra hop, so it's just as exposed to the
# model-router transient-500 flakiness CLAUDE.md's known quirks section
# documents at length.
POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = 420.0
WEBHOOK_SECRET = "duta-dev-webhook-secret-never-use-in-production"


def _submit_job(
    kong_url: str,
    token: str,
    *,
    question: str = "Berapa sisa cuti saya?",
    priority: str = "standard",
    idempotency_key: str | None = None,
    callback_url: str | None = None,
    callback_secret_ref: str | None = None,
) -> httpx.Response:
    body: dict[str, object] = {
        "agent_id": "hr-assistant",
        "input": {"type": "text", "content": question},
        "priority": priority,
    }
    if callback_url is not None:
        body["callback_url"] = callback_url
    if callback_secret_ref is not None:
        body["callback_secret_ref"] = callback_secret_ref
    return httpx.post(
        f"{kong_url}/v1/agent/jobs",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": idempotency_key or str(uuid.uuid4()),
        },
        json=body,
        timeout=30.0,
    )


def _get_job(kong_url: str, token: str, job_id: str) -> httpx.Response:
    return httpx.get(
        f"{kong_url}/v1/agent/jobs/{job_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )


def _poll_until_terminal(kong_url: str, token: str, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        resp = _get_job(kong_url, token, job_id)
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] in ("succeeded", "dead_lettered", "failed"):
            # "failed" is non-terminal in the DB sense (still eligible
            # for redelivery/retry) but the async-worker's own retry
            # loop drives it forward — for THIS suite's purposes, a
            # request that just wants "the run finished" should keep
            # polling past a transient "failed" the same way a human
            # operator watching the dashboard would.
            if last["status"] != "failed":
                return last
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"job {job_id} did not reach a terminal state: {last}")


def test_async_job_runs_end_to_end_through_kong_and_worker(
    kong_url: str, mint_jwt: Callable[..., str]
) -> None:
    token = mint_jwt(
        user_id="usr_budi",
        employee_id="emp_001",
        permissions=["policy.read", "leave.balance.read", "leave.request.create", "payslip.read"],
    )

    submit_resp = _submit_job(kong_url, token, question="Berapa sisa cuti saya?")
    assert submit_resp.status_code == 202, submit_resp.text
    accepted = submit_resp.json()
    assert accepted["status"] == "queued"
    job_id = accepted["job_id"]

    final = _poll_until_terminal(kong_url, token, job_id)

    assert final["status"] == "succeeded", final
    assert final["result"] is not None
    assert final["result"]["output"]["content"]
    assert final["error"] is None
    assert final["completed_at"] is not None


def test_job_status_is_tenant_scoped(kong_url: str, mint_jwt: Callable[..., str]) -> None:
    owner_token = mint_jwt(tenant_id="tnt_demo", user_id="usr_budi")
    submit_resp = _submit_job(kong_url, owner_token, question="Berapa sisa cuti saya?")
    assert submit_resp.status_code == 202, submit_resp.text
    job_id = submit_resp.json()["job_id"]

    # A token for a different tenant must not be able to read this job's
    # status at all — not even a "not authorized", a plain 404 (§7's
    # tenant boundary: existence itself is tenant-scoped information).
    other_tenant_token = mint_jwt(tenant_id="tnt_other", user_id="usr_someone_else")
    resp = _get_job(kong_url, other_tenant_token, job_id)
    assert resp.status_code == 404, resp.text


def test_idempotent_submission_returns_same_job_and_creates_one_row(
    kong_url: str, mint_jwt: Callable[..., str], db_conn: psycopg.Connection
) -> None:
    token = mint_jwt(user_id="usr_budi")
    idem_key = f"idem-m6-test-{uuid.uuid4().hex[:12]}"

    first = _submit_job(kong_url, token, idempotency_key=idem_key)
    assert first.status_code == 202, first.text
    job_id_1 = first.json()["job_id"]

    second = _submit_job(kong_url, token, idempotency_key=idem_key)
    assert second.status_code == 202, second.text
    job_id_2 = second.json()["job_id"]

    assert job_id_1 == job_id_2

    with db_conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("tnt_demo",))
        cur.execute("SELECT count(*) FROM jobs.async_jobs WHERE id = %s", (job_id_1,))
        (count,) = cur.fetchone()
    db_conn.commit()
    assert count == 1


def test_async_pool_quota_is_tracked_separately_from_sync(
    kong_url: str, mint_jwt: Callable[..., str], gateway_redis: redis.Redis
) -> None:
    # §6 L2 — a job submission reserves against `quota:{tenant}:async`,
    # never the `sync` pool's key, proving the two are genuinely separate
    # buckets rather than sharing one counter under different labels.
    token = mint_jwt(user_id="usr_budi")
    before = gateway_redis.get("quota:tnt_demo:async")
    before_value = int(before) if before is not None else 0

    submit_resp = _submit_job(kong_url, token, priority="bulk")
    assert submit_resp.status_code == 202, submit_resp.text

    after = gateway_redis.get("quota:tnt_demo:async")
    assert after is not None
    assert int(after) > before_value


class _WebhookReceiver:
    """Minimal HTTP server run in a background thread for the duration of
    one test, standing in for a tenant's real callback endpoint. Bound to
    0.0.0.0 so `async-worker` (a separate container reaching the host via
    `host.docker.internal`, the same Docker-Desktop-for-Mac mechanism
    CLAUDE.md's other entries rely on) can actually deliver to it.
    """

    def __init__(self) -> None:
        self.received: list[tuple[dict[str, str], bytes]] = []
        received = self.received

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received.append((dict(self.headers), body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self._server = http.server.HTTPServer(("0.0.0.0", 0), Handler)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _WebhookReceiver:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def webhook_receiver() -> Iterator[_WebhookReceiver]:
    with _WebhookReceiver() as receiver:
        yield receiver


def test_webhook_is_delivered_with_valid_hmac_signature_on_success(
    kong_url: str, mint_jwt: Callable[..., str], webhook_receiver: _WebhookReceiver
) -> None:
    token = mint_jwt(
        user_id="usr_budi",
        employee_id="emp_001",
        permissions=["policy.read", "leave.balance.read"],
    )
    callback_url = f"http://host.docker.internal:{webhook_receiver.port}/webhook"

    submit_resp = _submit_job(
        kong_url,
        token,
        question="Berapa sisa cuti saya?",
        callback_url=callback_url,
        callback_secret_ref="secret_ref_abc",
    )
    assert submit_resp.status_code == 202, submit_resp.text
    job_id = submit_resp.json()["job_id"]

    final = _poll_until_terminal(kong_url, token, job_id)
    assert final["status"] == "succeeded", final

    deadline = time.monotonic() + 30.0
    while not webhook_receiver.received and time.monotonic() < deadline:
        time.sleep(1.0)

    assert len(webhook_receiver.received) == 1, "webhook was never delivered"
    headers, body = webhook_receiver.received[0]
    signature = headers.get("X-Duta-Signature", "")
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)
