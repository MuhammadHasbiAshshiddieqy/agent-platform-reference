"""§5.9/§5.10's webhook delivery: HMAC-SHA256 signing (verified against a
manually-computed signature, the same check a real receiver would run —
`tests/integration/test_m6_async.py`'s test receiver does exactly this)
and retry-until-success / retry-exhausted behavior, with a near-zero
backoff schedule injected so this suite runs in milliseconds.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import httpx
import pytest
from async_worker.webhook import WebhookSender, sign
from contracts.jobs import WebhookPayload

PAYLOAD = WebhookPayload(
    job_id="job_test", trace_id="trc_test", status="succeeded", completed_at=datetime.now(UTC)
)


def test_sign_matches_a_manually_computed_hmac_sha256() -> None:
    body = b'{"hello":"world"}'
    secret = "shh"
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sign(body, secret) == expected


@pytest.mark.asyncio
async def test_send_signs_the_request_when_a_secret_is_resolved() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(200)

    sender = WebhookSender(secrets={"secret_ref_abc": "the-real-secret"}, timeout_seconds=5.0)
    sender._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    delivered = await sender.send(
        url="http://receiver.test/hook", secret_ref="secret_ref_abc", payload=PAYLOAD
    )

    assert delivered is True
    signature = captured["headers"]["X-Duta-Signature"]  # type: ignore[index]
    expected = sign(captured["body"], "the-real-secret")  # type: ignore[arg-type]
    assert signature == expected


@pytest.mark.asyncio
async def test_send_retries_until_success() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(500)
        return httpx.Response(200)

    sender = WebhookSender(secrets={}, timeout_seconds=5.0, backoff_seconds=[0.0, 0.0, 0.0, 0.0])
    sender._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    delivered = await sender.send(url="http://receiver.test/hook", secret_ref=None, payload=PAYLOAD)

    assert delivered is True
    assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_send_returns_false_after_exhausting_the_backoff_schedule() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    sender = WebhookSender(secrets={}, timeout_seconds=5.0, backoff_seconds=[0.0, 0.0])
    sender._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    delivered = await sender.send(url="http://receiver.test/hook", secret_ref=None, payload=PAYLOAD)

    assert delivered is False
