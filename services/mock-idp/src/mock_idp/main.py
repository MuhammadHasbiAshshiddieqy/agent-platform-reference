"""mock-idp (§28.10 ADR-009, §22.5) — dev login + RFC 8693 token exchange
over `seed/users.yaml`. HS256 shared-secret, not JWKS/RS256: this stack's
JWT trust anchor was already a dev-only HS256 secret shared between Kong
and `agent-gateway` since M1 (`config/kong/kong.yml`'s own docstring).
Making mock-idp issue real RS256 tokens would mean Kong's declarative JWT
plugin config needs to change too — a real production IdP swap-in would
need that regardless, so it's not a gap in the *pattern* (token exchange,
downscoping, 60s TTL, independent verification), just in the specific
signing algorithm this POC uses. `/.well-known/jwks.json` reflects that
honestly: an empty keyset, not a fabricated one nothing actually uses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Form, Header, HTTPException, status
from mock_idp.config import settings
from mock_idp.state import UserDirectory
from mock_idp.tokens import TokenError, mint_exchange_token, mint_login_token, verify_subject_token


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.directory = UserDirectory(settings.users_path)
    yield


app = FastAPI(title="mock-idp", lifespan=lifespan)


@app.post("/oauth/token")
async def token(body: dict[str, str]) -> dict[str, Any]:
    """Dev login — no password (§24.4's laptop-demo scope: this stands in
    for a real SSO flow, not a security boundary to replicate faithfully).
    `user_id` must be a seed user; that's the only check."""
    user_id = body.get("user_id")
    user = app.state.directory.get(user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unknown user_id")
    access_token, ttl = mint_login_token(user)
    return {"access_token": access_token, "token_type": "bearer", "expires_in": ttl}


@app.post("/oauth/eval-impersonate")
async def eval_impersonate(body: dict[str, str]) -> dict[str, Any]:
    """§13.7 — eval-service mints a token AS a seed user to run an item
    "as" that actor. Distinct from `/oauth/token` above (which will
    happily log in as anyone — a deliberate, documented POC
    simplification for that endpoint, not a security boundary) because
    THIS one is the literal mechanism §13.7 describes: "Batasan ini
    ditegakkan di IdP, bukan di eval-service" — the component asking for
    the privilege must not be the component deciding it has one, so the
    tenant check lives here, never left to eval-service's own judgment.
    """
    user_id = body.get("user_id")
    user = app.state.directory.get(user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unknown user_id")
    if user["tenant_id"] not in settings.eval_tenant_id_set:
        allowed = sorted(settings.eval_tenant_id_set)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"impersonation is only permitted for tenants in {allowed}",
        )
    access_token, ttl = mint_login_token(user)
    return {"access_token": access_token, "token_type": "bearer", "expires_in": ttl}


@app.post("/oauth/token-exchange")
async def token_exchange(
    grant_type: Annotated[str, Form()],
    subject_token: Annotated[str, Form()],
    subject_token_type: Annotated[str, Form()],
    audience: Annotated[str, Form()],
    scope: Annotated[str, Form()],
) -> dict[str, Any]:
    """RFC 8693 (§22.5) — verifies `subject_token` itself (mock-idp is the
    issuer, so it can), re-confirms the subject still exists, then mints a
    downscoped, 60-second, audience-bound token. `mock-business-api`
    verifies the *result* independently (`tokens.py`'s
    `verify_exchange_token`) — it does not call back here."""
    if grant_type != "urn:ietf:params:oauth:grant-type:token-exchange":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unsupported grant_type")
    if subject_token_type != "urn:ietf:params:oauth:token-type:jwt":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unsupported subject_token_type")

    try:
        claims = verify_subject_token(subject_token)
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.detail) from exc

    user = app.state.directory.get(claims["sub"])
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="subject no longer exists")

    access_token, ttl = mint_exchange_token(subject_claims=claims, audience=audience, scope=scope)
    return {
        "access_token": access_token,
        "issued_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "token_type": "bearer",
        "expires_in": ttl,
        "scope": scope,
    }


@app.get("/.well-known/jwks.json")
async def jwks() -> dict[str, Any]:
    return {"keys": []}


@app.get("/userinfo")
async def userinfo(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        claims = verify_subject_token(authorization.removeprefix("Bearer "))
    except TokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.detail) from exc
    return {
        "sub": claims["sub"],
        "tenant_id": claims["tenant_id"],
        "employee_id": claims.get("employee_id"),
        "roles": claims.get("roles", []),
        "permissions": claims.get("permissions", []),
        "scope_context": claims.get("scope_context", {}),
        "acl_group_ids": claims.get("acl_group_ids", []),
    }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ok"}
