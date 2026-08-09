"""§8.4's mutation contract, exercised in-process against the real FastAPI
app (httpx `ASGITransport` — no Docker needed, fast, but every header/
route/auth-check is the genuine code path, not a mock of it). Doubles as
part of the DoD's "risk_level: high cannot execute without approval" and
"double execution -> one effect" proofs (§15 M5 DoD).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
import pytest_asyncio
from mock_business_api.config import settings
from mock_business_api.main import app

BUDI = "usr_budi"  # emp_001, employee, tnt_demo — see seed/users.yaml
SITI = "usr_siti"  # emp_002, team_lead
DEWI = "usr_dewi"  # emp_004, finance — no leave.request.create permission
ANDI = "usr_andi"  # emp_003, hr_manager — has payroll.adjust


def _headers(
    *,
    tenant_id: str = "tnt_demo",
    actor_id: str = BUDI,
    idempotency_key: str | None = None,
    access_token: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-Trace-Id": "trc_test",
        "X-Tenant-Id": tenant_id,
        "X-Actor-Id": actor_id,
        "X-Actor-Type": "agent",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _exchange_token(
    *, sub: str = BUDI, tenant_id: str = "tnt_demo", scope: str, employee_id: str | None = None
) -> str:
    """Mints the exact shape mock-idp's `/oauth/token-exchange` would
    (`mock_idp.tokens.mint_exchange_token`) — minted directly here rather
    than via a live HTTP call to mock-idp, same reasoning as `tests/
    integration/conftest.py`'s `mint_jwt`: this file is testing business-
    api's OWN independent verification (`token_verification.py`), not
    mock-idp's issuance (that's `services/mock-idp/tests/integration/
    test_contract.py`'s job)."""
    now = int(time.time())
    payload = {
        "iss": settings.jwt_issuer,
        "sub": sub,
        "tenant_id": tenant_id,
        "employee_id": employee_id,
        "aud": "business-api",
        "scope": scope,
        "iat": now,
        "exp": now + 60,
    }
    return jwt.encode(payload, settings.jwt_signing_secret, algorithm="HS256")


@pytest_asyncio.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        # lifespan doesn't run automatically under ASGITransport unless we
        # drive it explicitly — reuse the app's own lifespan context so
        # app.state.business_state/preview_tokens/idempotency exist.
        async with app.router.lifespan_context(app):
            yield c


@pytest.mark.asyncio
async def test_get_leave_balance_returns_the_actors_own_balance(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/hr/v1/actions/get_leave_balance/query",
        headers=_headers(),
        json={"params": {"employee_id": "emp_001"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"employee_id": "emp_001", "leave_balance": 8}


@pytest.mark.asyncio
async def test_get_leave_balance_rejects_viewing_someone_elses_balance(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/hr/v1/actions/get_leave_balance/query",
        headers=_headers(actor_id=BUDI),
        json={"params": {"employee_id": "emp_002"}},  # Siti's, not Budi's
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_actor_without_required_permission_is_rejected(client: httpx.AsyncClient) -> None:
    # Every seeded role can submit their own leave requests, but Dewi
    # (finance) has no payroll.adjust — the demo's own point (§24.2) that
    # this action is deliberately restricted.
    resp = await client.post(
        "/payroll/v1/actions/adjust_payroll/preview",
        headers=_headers(actor_id=DEWI),
        json={"params": {"employee_id": "emp_004", "adjustment_percent": 5.0, "reason": "test"}},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_short_leave_request_previews_as_medium_risk_no_approval(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(access_token=_exchange_token(scope="leave:write", employee_id="emp_001")),
        json={"params": {"employee_id": "emp_001", "leave_days": 3, "start_date": "2026-09-01"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["risk_level"] == "medium"
    assert body["requires_approval"] is False
    assert body["preview_token"].startswith("prv_")
    assert body["effects"][0]["from"] == 8
    assert body["effects"][0]["to"] == 5


@pytest.mark.asyncio
async def test_long_leave_request_escalates_to_high_risk_requiring_approval(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(access_token=_exchange_token(scope="leave:write", employee_id="emp_001")),
        json={"params": {"employee_id": "emp_001", "leave_days": 7, "start_date": "2026-09-01"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["risk_level"] == "high"
    assert body["requires_approval"] is True


@pytest.mark.asyncio
async def test_execute_without_approval_id_is_rejected_when_approval_required(
    client: httpx.AsyncClient,
) -> None:
    preview = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(access_token=_exchange_token(scope="leave:write", employee_id="emp_001")),
        json={"params": {"employee_id": "emp_001", "leave_days": 7, "start_date": "2026-09-01"}},
    )
    token = preview.json()["preview_token"]

    resp = await client.post(
        "/hr/v1/actions/submit_leave_request/execute",
        headers=_headers(idempotency_key=str(uuid.uuid4())),
        json={"preview_token": token},  # no approval_id
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_execute_deducts_balance_and_is_idempotent_on_replay(
    client: httpx.AsyncClient,
) -> None:
    preview = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(access_token=_exchange_token(scope="leave:write", employee_id="emp_001")),
        json={"params": {"employee_id": "emp_001", "leave_days": 2, "start_date": "2026-09-01"}},
    )
    token = preview.json()["preview_token"]
    idem_key = str(uuid.uuid4())

    first = await client.post(
        "/hr/v1/actions/submit_leave_request/execute",
        headers=_headers(idempotency_key=idem_key),
        json={"preview_token": token},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "executed"

    balance = await client.post(
        "/hr/v1/actions/get_leave_balance/query",
        headers=_headers(),
        json={"params": {"employee_id": "emp_001"}},
    )
    assert balance.json()["leave_balance"] == 6

    # Replay with the SAME Idempotency-Key — must not deduct a second time.
    second = await client.post(
        "/hr/v1/actions/submit_leave_request/execute",
        headers=_headers(idempotency_key=idem_key),
        json={"preview_token": token},
    )
    assert second.status_code == 200
    assert second.json() == first.json()

    balance_after_replay = await client.post(
        "/hr/v1/actions/get_leave_balance/query",
        headers=_headers(),
        json={"params": {"employee_id": "emp_001"}},
    )
    assert balance_after_replay.json()["leave_balance"] == 6  # unchanged


@pytest.mark.asyncio
async def test_preview_token_cannot_be_reused_for_a_second_execute(
    client: httpx.AsyncClient,
) -> None:
    preview = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(access_token=_exchange_token(scope="leave:write", employee_id="emp_001")),
        json={"params": {"employee_id": "emp_001", "leave_days": 1, "start_date": "2026-09-01"}},
    )
    token = preview.json()["preview_token"]

    first = await client.post(
        "/hr/v1/actions/submit_leave_request/execute",
        headers=_headers(idempotency_key=str(uuid.uuid4())),
        json={"preview_token": token},
    )
    assert first.status_code == 200

    # Different Idempotency-Key this time — the guard that must fire now
    # is the preview_token's own single-use rule, not idempotency replay.
    second = await client.post(
        "/hr/v1/actions/submit_leave_request/execute",
        headers=_headers(idempotency_key=str(uuid.uuid4())),
        json={"preview_token": token},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_adjust_payroll_is_always_high_risk_and_requires_approval(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/payroll/v1/actions/adjust_payroll/preview",
        headers=_headers(
            actor_id=ANDI,  # hr_manager, has payroll.adjust
            access_token=_exchange_token(sub=ANDI, scope="payroll:write", employee_id="emp_003"),
        ),
        json={"params": {"employee_id": "emp_001", "adjustment_percent": 5.0, "reason": "promosi"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["risk_level"] == "high"
    assert body["requires_approval"] is True


@pytest.mark.asyncio
async def test_preview_rejects_missing_access_token_for_a_scope_required_action(
    client: httpx.AsyncClient,
) -> None:
    # §22.5 step 5 — independent verification: even with a valid X-Actor-*
    # identity (authorize() already passed), no Authorization header at
    # all must still be rejected for an action requiring token exchange.
    resp = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(),  # no access_token
        json={"params": {"employee_id": "emp_001", "leave_days": 2, "start_date": "2026-09-01"}},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_preview_rejects_an_access_token_with_the_wrong_scope(
    client: httpx.AsyncClient,
) -> None:
    access_token = _exchange_token(scope="payroll:write", employee_id="emp_001")
    resp = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(access_token=access_token),
        json={"params": {"employee_id": "emp_001", "leave_days": 2, "start_date": "2026-09-01"}},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_preview_rejects_an_access_token_minted_for_a_different_actor(
    client: httpx.AsyncClient,
) -> None:
    # Token says usr_siti, X-Actor-Id says usr_budi — the two identity
    # signals disagree, and the independent check must catch that even
    # though the header-based authorize() call alone would have passed.
    access_token = _exchange_token(sub=SITI, scope="leave:write", employee_id="emp_002")
    resp = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(actor_id=BUDI, access_token=access_token),
        json={"params": {"employee_id": "emp_001", "leave_days": 2, "start_date": "2026-09-01"}},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_execute_missing_headers_returns_400(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/hr/v1/actions/get_leave_balance/query",
        json={"params": {"employee_id": "emp_001"}},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_agent_actor_type_is_rejected(client: httpx.AsyncClient) -> None:
    headers = _headers()
    headers["X-Actor-Type"] = "user"
    resp = await client.post(
        "/hr/v1/actions/get_leave_balance/query",
        headers=headers,
        json={"params": {"employee_id": "emp_001"}},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_simulate_error_500_triggers_on_any_call(client: httpx.AsyncClient) -> None:
    headers = _headers()
    headers["X-Simulate"] = "error_500"
    resp = await client.post(
        "/hr/v1/actions/get_leave_balance/query",
        headers=headers,
        json={"params": {"employee_id": "emp_001"}},
    )
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_simulate_rate_limit_returns_429_with_retry_after(client: httpx.AsyncClient) -> None:
    headers = _headers()
    headers["X-Simulate"] = "rate_limit"
    resp = await client.post(
        "/hr/v1/actions/get_leave_balance/query",
        headers=headers,
        json={"params": {"employee_id": "emp_001"}},
    )
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "5"


@pytest.mark.asyncio
async def test_simulate_partial_failure_lets_preview_succeed_but_execute_fail(
    client: httpx.AsyncClient,
) -> None:
    preview = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(access_token=_exchange_token(scope="leave:write", employee_id="emp_001")),
        json={"params": {"employee_id": "emp_001", "leave_days": 1, "start_date": "2026-09-01"}},
    )
    assert preview.status_code == 200
    token = preview.json()["preview_token"]

    headers = _headers(idempotency_key=str(uuid.uuid4()))
    headers["X-Simulate"] = "partial_failure"
    execute = await client.post(
        "/hr/v1/actions/submit_leave_request/execute",
        headers=headers,
        json={"preview_token": token},
    )
    assert execute.status_code == 502


@pytest.mark.asyncio
async def test_reset_restores_seeded_leave_balance(client: httpx.AsyncClient) -> None:
    preview = await client.post(
        "/hr/v1/actions/submit_leave_request/preview",
        headers=_headers(access_token=_exchange_token(scope="leave:write", employee_id="emp_001")),
        json={"params": {"employee_id": "emp_001", "leave_days": 4, "start_date": "2026-09-01"}},
    )
    token = preview.json()["preview_token"]
    await client.post(
        "/hr/v1/actions/submit_leave_request/execute",
        headers=_headers(idempotency_key=str(uuid.uuid4())),
        json={"preview_token": token},
    )

    reset_resp = await client.post("/internal/v1/reset")
    assert reset_resp.status_code == 200

    balance = await client.post(
        "/hr/v1/actions/get_leave_balance/query",
        headers=_headers(),
        json={"params": {"employee_id": "emp_001"}},
    )
    assert balance.json()["leave_balance"] == 8
