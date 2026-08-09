"""§7.1 — JWT already validated at the edge by Kong; the gateway
re-verifies anyway rather than trusting an unverified decode. Same
principle §8.4 states explicitly for business-api ("jangan pernah
mempercayai bahwa harness sudah mengecek"): the component one hop
downstream of a trust boundary doesn't take the upstream's word for it.

HS256 shared secret is a dev-only stand-in for mock-idp's JWKS/RS256
(§28.10 ADR-009's real-production shape) — `mock-idp` (M5b) issues these
tokens with the exact §22.3 claim shape now, over the same shared secret
Kong's declarative JWT plugin already validates against. JWKS/RS256
would need Kong's plugin config to change too; documented as a POC-scope
simplification (see `services/mock-idp/src/mock_idp/main.py`'s
docstring), not a gap in the token-exchange *pattern* itself.
"""

from __future__ import annotations

from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from gateway.config import settings


class AuthContext:
    def __init__(
        self,
        tenant_id: str,
        user_id: str,
        acl_group_ids: list[str],
        employee_id: str | None,
        permissions: list[str],
        roles: list[str],
        scope_context: dict[str, Any],
        raw_token: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.acl_group_ids = acl_group_ids
        self.employee_id = employee_id
        self.permissions = permissions
        self.roles = roles
        self.scope_context = scope_context
        # §22.5 RFC 8693 subject_token — forwarded to harness as-is so it
        # can request a downscoped token per tool call. Never logged.
        self.raw_token = raw_token


def _extract_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization")
    if not header or not header.startswith("Bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Missing or malformed Authorization header"
        )
    return header.removeprefix("Bearer ")


def authenticate(request: Request) -> AuthContext:
    token = _extract_bearer_token(request)
    try:
        claims = jwt.decode(token, settings.jwt_signing_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid JWT") from exc

    tenant_id = claims.get("tenant_id")
    user_id = claims.get("sub")
    if not tenant_id or not user_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="JWT missing required tenant_id/sub claims"
        )
    return AuthContext(
        tenant_id=tenant_id,
        user_id=user_id,
        acl_group_ids=claims.get("acl_group_ids", []),
        employee_id=claims.get("employee_id"),
        permissions=claims.get("permissions", []),
        roles=claims.get("roles", []),
        scope_context=claims.get("scope_context", {}),
        raw_token=token,
    )
