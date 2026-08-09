"""§8.4's hard rule, verbatim: "Business API wajib mengecek otorisasi
X-Actor-Id sendiri. Jangan pernah mempercayai bahwa harness sudah
mengecek." This module is that independent check — it reads straight
from `seed/users.yaml` (state.py), the same file mock-idp will read from
in M5b (ADR-009), rather than trusting anything the caller asserts about
itself.

`REQUIRED_PERMISSIONS` and the self-scope enforcement below are a
deliberately small slice of §22.2's `data_scope` concept — just enough
for the three §24.2 actions to check "does this actor even have this
permission" and "is this actor acting on their own employee record".
Team/department/tenant-scope resolution and the full tool-manifest-driven
`PolicyResolver` are M5b's job (§22.1); duplicating that machinery here
for three fixed actions isn't worth it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from mock_business_api.state import BusinessState
from mock_business_api.token_verification import TokenVerificationError, verify_exchange_token

REQUIRED_PERMISSIONS = {
    "get_leave_balance": "leave.balance.read",
    "submit_leave_request": "leave.request.create",
    "adjust_payroll": "payroll.adjust",
}

# §22.5 — only `preview` calls for these two mutation actions carry a
# token-exchange access_token (see harness/tools/executor.py's docstring
# for why `execute` doesn't); `get_leave_balance` has no declared
# required_scopes_for_token_exchange in its manifest, so it's absent here.
TOKEN_EXCHANGE_REQUIRED_SCOPES = {
    "submit_leave_request": "leave:write",
    "adjust_payroll": "payroll:write",
}

# Actions whose params.employee_id must equal the actor's own employee_id
# (§22.2's `data_scope: self`). `adjust_payroll` is `data_scope: tenant`
# (§24.2) — no such restriction.
SELF_SCOPED_ACTIONS = {"get_leave_balance", "submit_leave_request"}


@dataclass
class ActorHeaders:
    trace_id: str
    tenant_id: str
    actor_id: str
    actor_type: str
    idempotency_key: str | None


def extract_actor_headers(request: Request, *, require_idempotency: bool) -> ActorHeaders:
    trace_id = request.headers.get("X-Trace-Id")
    tenant_id = request.headers.get("X-Tenant-Id")
    actor_id = request.headers.get("X-Actor-Id")
    actor_type = request.headers.get("X-Actor-Type")
    idempotency_key = request.headers.get("Idempotency-Key")

    missing = [
        name
        for name, value in [
            ("X-Trace-Id", trace_id),
            ("X-Tenant-Id", tenant_id),
            ("X-Actor-Id", actor_id),
            ("X-Actor-Type", actor_type),
        ]
        if not value
    ]
    if require_idempotency and not idempotency_key:
        missing.append("Idempotency-Key")
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"missing required headers: {', '.join(missing)}"
        )

    assert trace_id and tenant_id and actor_id and actor_type  # narrowed by the check above
    return ActorHeaders(
        trace_id=trace_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_type=actor_type,
        idempotency_key=idempotency_key,
    )


def authorize(state: BusinessState, headers: ActorHeaders, *, action: str) -> dict[str, Any]:
    if headers.actor_type != "agent":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="only actor_type=agent may call this API"
        )

    user = state.users.get(headers.actor_id)
    if user is None or user["tenant_id"] != headers.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="unknown actor for this tenant")

    required_permission = REQUIRED_PERMISSIONS[action]
    if required_permission not in user.get("permissions", []):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"actor lacks required permission: {required_permission}",
        )
    return user


def verify_token_exchange(request: Request, headers: ActorHeaders, *, action: str) -> None:
    """§22.5 step 5 — independent verification of the downscoped access
    token, for whichever actions declare `required_scopes_for_token_
    exchange` in their manifest (§22.2). A no-op for actions that don't
    (§8.4's `X-Actor-*` header check above is still the primary gate for
    those, unchanged since M5)."""
    required_scope = TOKEN_EXCHANGE_REQUIRED_SCOPES.get(action)
    if required_scope is None:
        return

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=f"missing token-exchange access_token for scope {required_scope}",
        )
    try:
        claims = verify_exchange_token(auth_header.removeprefix("Bearer "))
    except TokenVerificationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=exc.detail) from exc

    if required_scope not in claims.get("scope", "").split():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"access_token missing required scope: {required_scope}",
        )
    if claims.get("sub") != headers.actor_id or claims.get("tenant_id") != headers.tenant_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="access_token identity does not match X-Actor-Id/X-Tenant-Id",
        )


def enforce_self_scope(user: dict[str, Any], *, action: str, employee_id: str) -> None:
    if action not in SELF_SCOPED_ACTIONS:
        return
    if employee_id != user.get("employee_id"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=f"actor may only act on their own employee record for {action}",
        )
