"""M2 DoD (§15/§6): a tenant over quota gets 429 with an accurate
Retry-After; other tenants are unaffected; reconciliation returns the
bucket to the exact right value after a run.

Quota logic itself (reserve/reject/reconcile/sweep, Lua-script atomicity)
is unit-tested against a real Redis in
services/gateway/tests/integration/test_quota.py — this file only proves
the DoD's actual claim: that behavior is reachable through the live
`POST /v1/agent/invoke` path, through Kong, for real.

Rather than sending 500,000 real tokens' worth of requests to trigger a
rejection (impractical for a test suite), these tests seed the tenant's
Redis counter directly to just under the limit — deterministic, and
proves the same code path a real load test would exercise (the endpoint
doesn't know or care whether the counter got there via real traffic or a
seeded value).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime

import httpx
import pytest
import redis

SYNC_LIMIT = 500_000  # gateway.config.Settings.sync_tokens_per_hour default


def _invoke_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture(autouse=True)
def _cleanup_quota_keys(gateway_redis: redis.Redis):
    yield
    for key in gateway_redis.scan_iter(match="quota:tnt_m2_*"):
        gateway_redis.delete(key)


def test_quota_exceeded_returns_429_with_retry_after_and_reset_time(
    kong_url: str, mint_jwt: Callable[..., str], gateway_redis: redis.Redis
) -> None:
    tenant_id = f"tnt_m2_reject_{uuid.uuid4().hex[:8]}"
    gateway_redis.set(f"quota:{tenant_id}:sync", SYNC_LIMIT - 1, ex=3600)

    token = mint_jwt(tenant_id=tenant_id)
    resp = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_invoke_headers(token),
        json={"agent_id": "m2-quota-test", "input": {"type": "text", "content": "x" * 200}},
        timeout=30.0,
    )

    assert resp.status_code == 429, resp.text
    retry_after = int(resp.headers["Retry-After"])
    assert 0 < retry_after <= 3600

    body = resp.json()["detail"]
    assert body["quota_reset_at"]
    reset_at = datetime.fromisoformat(body["quota_reset_at"])
    assert reset_at.timestamp() > time.time()


def test_quota_rejection_for_one_tenant_does_not_affect_another(
    kong_url: str, mint_jwt: Callable[..., str], gateway_redis: redis.Redis
) -> None:
    busy_tenant = f"tnt_m2_busy_{uuid.uuid4().hex[:8]}"
    quiet_tenant = f"tnt_m2_quiet_{uuid.uuid4().hex[:8]}"
    gateway_redis.set(f"quota:{busy_tenant}:sync", SYNC_LIMIT - 1, ex=3600)

    busy_resp = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_invoke_headers(mint_jwt(tenant_id=busy_tenant)),
        json={"agent_id": "m2-quota-test", "input": {"type": "text", "content": "x" * 200}},
        timeout=30.0,
    )
    assert busy_resp.status_code == 429

    # busy_tenant's rejection must never have touched quiet_tenant's bucket
    # — the isolation claim the M2 DoD names explicitly.
    quiet_bucket = gateway_redis.get(f"quota:{quiet_tenant}:sync")
    assert quiet_bucket is None


def test_reconciliation_leaves_the_bucket_at_exact_actual_usage(
    kong_url: str, mint_jwt: Callable[..., str], gateway_redis: redis.Redis
) -> None:
    tenant_id = f"tnt_m2_reconcile_{uuid.uuid4().hex[:8]}"
    token = mint_jwt(tenant_id=tenant_id)

    resp = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_invoke_headers(token),
        json={
            "agent_id": "m2-quota-test",
            "input": {"type": "text", "content": "Reply with exactly one word: reconciled."},
        },
        timeout=160.0,
    )
    assert resp.status_code == 200, resp.text
    usage = resp.json()["usage"]
    expected = usage["input_tokens"] + usage["output_tokens"]

    bucket = gateway_redis.get(f"quota:{tenant_id}:sync")
    assert bucket is not None
    assert int(bucket) == expected

    # The reservation itself must be gone — reconciled, not left dangling
    # for the sweeper to (wrongly) find later.
    reservation_keys = list(gateway_redis.scan_iter(match=f"quota:reservation:{tenant_id}:*"))
    assert reservation_keys == []
