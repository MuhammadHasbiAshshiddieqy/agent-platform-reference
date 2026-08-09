"""§22.9's live proof, end to end through Kong (not just unit-level): a
real login via `mock-idp`, a real tool-calling run with the resulting
JWT, and a real killswitch flip via the gateway's admin endpoint. The
30+ case behavioral suite (permission matrix, tool leakage, data_scope,
audience isolation) lives in `services/harness/tests/unit/test_yaml_
policy_resolver.py` and `tests/conformance/test_policy_resolver.py` as
fast, deterministic tests — this file only proves the wiring is real:
mock-idp's issued token is what Kong/gateway/harness actually accept,
and the killswitch admin endpoint actually reaches the same Redis/DB
the running harness reads from.

No seed user has `platform.killswitch.manage` (§26.1's seed doesn't
model a platform-admin role) — the killswitch tests mint a token
directly with the shared secret, same reasoning as `tests/integration/
conftest.py`'s `mint_jwt` for every other milestone's admin-shaped case.
"""

from __future__ import annotations

import time
import uuid

import httpx
import jwt
import psycopg
import pytest


def _mock_idp_login(mock_idp_url: str, user_id: str) -> str:
    resp = httpx.post(f"{mock_idp_url}/oauth/token", json={"user_id": user_id}, timeout=10.0)
    assert resp.status_code == 200, resp.text
    token: str = resp.json()["access_token"]
    return token


def _mint_admin_token(env: dict[str, str]) -> str:
    now = int(time.time())
    payload = {
        "iss": "duta-demo",
        "sub": "usr_admin_test",
        "tenant_id": "tnt_demo",
        "acl_group_ids": [],
        "employee_id": None,
        "permissions": ["platform.killswitch.manage"],
        "roles": ["platform_admin"],
        "scope_context": {},
        "iat": now,
        "exp": now + 900,
    }
    return jwt.encode(payload, env["JWT_SIGNING_SECRET"], algorithm="HS256")


def _invoke(kong_url: str, token: str, question: str) -> httpx.Response:
    return httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        json={
            "agent_id": "hr-assistant",
            "input": {"type": "text", "content": question},
            "options": {"allow_mutations": True},
        },
        timeout=240.0,
    )


@pytest.fixture()
def mock_idp_url() -> str:
    return "http://localhost:8087"


def test_mock_idp_issued_token_is_accepted_by_kong_and_rbac_is_enforced(
    kong_url: str, mock_idp_url: str, db_conn: psycopg.Connection
) -> None:
    # usr_budi: employee, no payroll.adjust — a real login, not a
    # hand-minted test token, exercising the actual §22.5-adjacent claim
    # issuance path (mock-idp reading seed/users.yaml).
    token = _mock_idp_login(mock_idp_url, "usr_budi")

    resp = _invoke(kong_url, token, "Berapa sisa cuti saya?")
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    # Scoped by run_id, not a "most recent N" heuristic — this suite's
    # other tests (and any parallel/adjacent live testing) also write
    # audit.authz_decisions rows with the same actor_user_id, and several
    # rows can legitimately share one `created_at` (one row per candidate
    # tool, inserted in the same batch) — an ORDER BY created_at DESC
    # LIMIT approach can silently mix rows from a different run.
    with db_conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("tnt_demo",))
        cur.execute(
            "SELECT tool_name, decision, deny_reason FROM audit.authz_decisions WHERE run_id = %s",
            (run_id,),
        )
        rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    db_conn.commit()

    assert rows["get_leave_balance"][0] == "allow"
    assert rows["adjust_payroll"][0] == "deny"
    assert rows["adjust_payroll"][1] == "missing required permissions"


def test_admin_without_killswitch_permission_is_rejected(kong_url: str, mock_idp_url: str) -> None:
    token = _mock_idp_login(mock_idp_url, "usr_budi")  # no platform.killswitch.manage
    resp = httpx.post(
        f"{kong_url}/v1/admin/killswitch/tools/get_leave_balance",
        headers={"Authorization": f"Bearer {token}"},
        json={"disabled": True, "reason": "test"},
        timeout=30.0,
    )
    assert resp.status_code == 403


def test_killswitch_blocks_and_unblocks_a_tool_live(
    kong_url: str, mock_idp_url: str, env: dict[str, str], db_conn: psycopg.Connection
) -> None:
    admin_token = _mint_admin_token(env)

    disable_resp = httpx.post(
        f"{kong_url}/v1/admin/killswitch/tools/get_leave_balance",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"disabled": True, "reason": "live integration test"},
        timeout=30.0,
    )
    assert disable_resp.status_code == 200, disable_resp.text
    assert disable_resp.json() == {
        "name": "get_leave_balance",
        "kind": "tool",
        "disabled": True,
    }

    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT disabled, changed_by FROM authz.tool_overrides "
                "WHERE tool_name = 'get_leave_balance'"
            )
            row = cur.fetchone()
        db_conn.commit()
        assert row is not None
        assert row[0] is True
        assert row[1] == "usr_admin_test"

        budi_token = _mock_idp_login(mock_idp_url, "usr_budi")
        resp = _invoke(kong_url, budi_token, "Berapa sisa cuti saya?")
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]

        with db_conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("tnt_demo",))
            cur.execute(
                "SELECT decision, deny_reason FROM audit.authz_decisions "
                "WHERE run_id = %s AND tool_name = 'get_leave_balance'",
                (run_id,),
            )
            decision_row = cur.fetchone()
        db_conn.commit()
        assert decision_row is not None
        assert decision_row[0] == "deny"
        assert decision_row[1] == "tool disabled by killswitch"
    finally:
        # Always re-enable — a failed assertion above must not leave the
        # tool disabled for every later test in the suite.
        httpx.post(
            f"{kong_url}/v1/admin/killswitch/tools/get_leave_balance",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"disabled": False},
            timeout=30.0,
        )
