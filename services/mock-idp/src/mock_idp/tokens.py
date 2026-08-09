"""JWT minting/verification. §22.3's login claim shape (full permissions/
roles/scope_context/acl_group_ids) vs. §22.5's exchange claim shape
(downscoped: identity + one `scope`, no permission list at all) are two
deliberately different token shapes minted by the two functions below —
business-api only ever sees the downscoped kind.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
from mock_idp.config import settings


class TokenError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def mint_login_token(user: dict[str, Any]) -> tuple[str, int]:
    now = int(time.time())
    ttl = int(settings.login_token_ttl_seconds)
    payload = {
        "iss": settings.jwt_issuer,
        "sub": user["user_id"],
        "tenant_id": user["tenant_id"],
        "employee_id": user.get("employee_id"),
        "roles": [user["role"]],
        "permissions": user.get("permissions", []),
        "scope_context": user.get("scope_context", {}),
        "acl_group_ids": user.get("acl_group_ids", []),
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.jwt_signing_secret, algorithm="HS256"), ttl


def verify_subject_token(token: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token, settings.jwt_signing_secret, algorithms=["HS256"], issuer=settings.jwt_issuer
        )
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"invalid subject_token: {exc}") from exc
    return claims


def mint_exchange_token(
    *, subject_claims: dict[str, Any], audience: str, scope: str
) -> tuple[str, int]:
    now = int(time.time())
    ttl = int(settings.exchange_token_ttl_seconds)
    payload = {
        "iss": settings.jwt_issuer,
        "sub": subject_claims["sub"],
        "tenant_id": subject_claims["tenant_id"],
        "employee_id": subject_claims.get("employee_id"),
        "aud": audience,
        "scope": scope,
        # Deliberately no `permissions`/`roles`/`acl_group_ids` — §22.5's
        # whole point is that a compromised harness only ever holds a
        # token scoped to one action for 60 seconds, not the requester's
        # full permission set.
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.jwt_signing_secret, algorithm="HS256"), ttl


def verify_exchange_token(token: str, *, audience: str) -> dict[str, Any]:
    """Independent verification, business-api's side (§22.5 step 5) — same
    shared secret, no network round-trip back to mock-idp, mirroring how
    `gateway/auth.py` re-verifies Kong's already-validated JWT locally
    rather than calling an IdP endpoint per request."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_signing_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=audience,
        )
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"invalid access_token: {exc}") from exc
    return claims
