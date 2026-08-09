"""§22.5/§28.10 ADR-009's mock-idp contract, exercised in-process against
the real FastAPI app (httpx `ASGITransport`, same pattern as
services/mock-business-api/tests/integration/test_contract.py) — every
route/claim-shape/verification is the genuine code path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
import pytest_asyncio
from mock_idp.config import settings
from mock_idp.main import app

BUDI = "usr_budi"  # emp_001, employee, tnt_demo — see seed/users.yaml


@pytest_asyncio.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def _login(client: httpx.AsyncClient, user_id: str = BUDI) -> str:
    resp = await client.post("/oauth/token", json={"user_id": user_id})
    assert resp.status_code == 200, resp.text
    token: str = resp.json()["access_token"]
    return token


@pytest.mark.asyncio
async def test_login_issues_a_token_with_the_full_22_3_claim_shape(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post("/oauth/token", json={"user_id": BUDI})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == int(settings.login_token_ttl_seconds)

    claims = jwt.decode(
        body["access_token"], settings.jwt_signing_secret, algorithms=["HS256"], issuer="duta-demo"
    )
    assert claims["sub"] == BUDI
    assert claims["tenant_id"] == "tnt_demo"
    assert claims["employee_id"] == "emp_001"
    assert claims["roles"] == ["employee"]
    assert "leave.request.create" in claims["permissions"]
    assert "grp_all_staff" in claims["acl_group_ids"]


@pytest.mark.asyncio
async def test_login_rejects_unknown_user_id(client: httpx.AsyncClient) -> None:
    resp = await client.post("/oauth/token", json={"user_id": "usr_does_not_exist"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_exchange_issues_a_downscoped_60_second_token(
    client: httpx.AsyncClient,
) -> None:
    subject_token = await _login(client)
    resp = await client.post(
        "/oauth/token-exchange",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": subject_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "audience": "business-api",
            "scope": "leave:write",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_in"] == 60
    assert body["scope"] == "leave:write"

    claims = jwt.decode(
        body["access_token"],
        settings.jwt_signing_secret,
        algorithms=["HS256"],
        issuer="duta-demo",
        audience="business-api",
    )
    assert claims["sub"] == BUDI
    assert claims["tenant_id"] == "tnt_demo"
    assert claims["scope"] == "leave:write"
    # §22.5's whole point — the downscoped token carries identity + one
    # scope, not the requester's full permission set.
    assert "permissions" not in claims
    assert "roles" not in claims


@pytest.mark.asyncio
async def test_token_exchange_rejects_a_garbage_subject_token(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/oauth/token-exchange",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": "not-a-real-jwt",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "audience": "business-api",
            "scope": "leave:write",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_exchange_rejects_wrong_grant_type(client: httpx.AsyncClient) -> None:
    subject_token = await _login(client)
    resp = await client.post(
        "/oauth/token-exchange",
        data={
            "grant_type": "authorization_code",
            "subject_token": subject_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "audience": "business-api",
            "scope": "leave:write",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_userinfo_returns_claims_for_a_valid_bearer_token(client: httpx.AsyncClient) -> None:
    token = await _login(client)
    resp = await client.get("/userinfo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sub"] == BUDI
    assert body["employee_id"] == "emp_001"


@pytest.mark.asyncio
async def test_userinfo_rejects_missing_bearer_token(client: httpx.AsyncClient) -> None:
    resp = await client.get("/userinfo")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jwks_returns_an_empty_keyset_since_this_poc_uses_hs256(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    assert resp.json() == {"keys": []}
