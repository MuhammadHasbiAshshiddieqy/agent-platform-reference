"""§22.5 step 5's independent verification. Deliberately NOT imported
from `mock-idp` — services never import each other (§4.1) — this is
mock-business-api's *own* copy of the same shared-secret check, exactly
the "never trust that the caller already verified it" principle §8.4
states for `X-Actor-*` headers, applied to the token-exchange token too.
"""

from __future__ import annotations

from typing import Any

import jwt

from mock_business_api.config import settings


class TokenVerificationError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def verify_exchange_token(token: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_signing_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience="business-api",
        )
    except jwt.InvalidTokenError as exc:
        raise TokenVerificationError(f"invalid access_token: {exc}") from exc
    return claims
