"""M5 DoD (§15/§26.2 steps 4-5): a `risk_level: high` mutation cannot
execute without approval, double-executing the same approval decision
produces exactly one effect, and an approver without the required
permission is rejected. The 30+ case behavioral suite (risk branching,
self-scope forcing, idempotency, unauthorized attempts) lives in
services/harness/tests/unit/ (test_tool_executor.py, test_graph_tools.py,
test_mutation_safety_metric.py) and services/mock-business-api/tests/
integration/test_contract.py as fast, deterministic tests — this file
only proves the wiring is real end to end through Kong.

Deliberately phrases the leave-request prompt as "Saya ingin mengajukan
cuti selama 7 hari kerja mulai tanggal 1 September 2026." rather than the
more terse "Tolong ajukan cuti 7 hari..." from §26.2's own demo script —
manual live testing found the terser imperative phrasing occasionally
tripped this dev environment's `agent-local` (qwen2.5:3b) fallback
guardrail classifiers into a false-positive block (reproducible,
temperature=0, yet inconsistent with the same prompt tested standalone —
see CLAUDE.md's known quirks). The classifiers' own logic is already unit
-tested with mocked responses (test_injection.py, test_offtopic.py); this
file needs a prompt that reliably clears them on the live weak fallback
model so it's testing the *mutation flow*, not classifier accuracy.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import psycopg


def _invoke_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}


def _invoke(
    kong_url: str, token: str, question: str, *, allow_mutations: bool = False
) -> httpx.Response:
    return httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_invoke_headers(token),
        json={
            "agent_id": "hr-assistant",
            "input": {"type": "text", "content": question},
            "options": {"allow_mutations": allow_mutations},
        },
        timeout=240.0,
    )


def _decide_approval(kong_url: str, token: str, approval_id: str, decision: str) -> httpx.Response:
    return httpx.post(
        f"{kong_url}/v1/approvals/{approval_id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": decision},
        timeout=60.0,
    )


def test_high_risk_leave_request_requires_approval_and_does_not_execute(
    kong_url: str, mint_jwt: Callable[..., str], db_conn: psycopg.Connection
) -> None:
    token = mint_jwt(
        tenant_id="tnt_demo",
        user_id="usr_budi",
        acl_group_ids=["grp_all_staff"],
        employee_id="emp_001",
        permissions=["policy.read", "leave.balance.read", "leave.request.create", "payslip.read"],
    )
    resp = _invoke(
        kong_url,
        token,
        "Saya ingin mengajukan cuti selama 7 hari kerja mulai tanggal 1 September 2026.",
        allow_mutations=True,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["pending_approvals"]) == 1
    approval = body["pending_approvals"][0]
    assert approval["risk_level"] == "high"
    assert approval["action_name"] == "submit_leave_request"

    with db_conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("tnt_demo",))
        cur.execute(
            "SELECT status, risk_level FROM audit.mutation_requests WHERE approval_id = %s",
            (approval["approval_id"],),
        )
        row = cur.fetchone()
    db_conn.commit()
    assert row is not None
    assert row[0] == "awaiting_approval"
    assert row[1] == "high"


def test_approval_executes_exactly_once_even_when_the_decision_is_replayed(
    kong_url: str, mint_jwt: Callable[..., str], db_conn: psycopg.Connection
) -> None:
    budi = mint_jwt(
        tenant_id="tnt_demo",
        user_id="usr_budi",
        acl_group_ids=["grp_all_staff"],
        employee_id="emp_001",
        permissions=["policy.read", "leave.balance.read", "leave.request.create", "payslip.read"],
    )
    siti = mint_jwt(
        tenant_id="tnt_demo",
        user_id="usr_siti",
        acl_group_ids=["grp_all_staff"],
        employee_id="emp_002",
        permissions=[
            "policy.read",
            "leave.balance.read",
            "leave.request.create",
            "payslip.read",
            "leave.request.approve",
        ],
    )

    invoke_resp = _invoke(
        kong_url,
        budi,
        "Saya ingin mengajukan cuti selama 6 hari kerja mulai tanggal 1 Oktober 2026.",
        allow_mutations=True,
    )
    assert invoke_resp.status_code == 200, invoke_resp.text
    approvals = invoke_resp.json()["pending_approvals"]
    # Usually exactly 1 — but MAX_TOOL_ITERATIONS permits up to 2 real
    # tool calls in one turn, and this dev environment's weak `agent-
    # local` fallback occasionally re-requests `submit_leave_request` a
    # second time with the same params instead of stopping after the
    # first result (the same class of finding documented in
    # graph/build.py's MAX_TOOL_ITERATIONS comment and CLAUDE.md — not a
    # bug in the mutation flow itself, each call independently produces a
    # correct preview -> awaiting_approval). Only the first approval is
    # this test's concern; the idempotency claim below is unaffected by
    # how many were created.
    assert len(approvals) >= 1
    approval_id = approvals[0]["approval_id"]

    first = _decide_approval(kong_url, siti, approval_id, "approve")
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["mutation_status"] == "executed"
    assert first_body["business_ref"]

    # §23.2i: replay the identical decision — must not execute a second
    # time (business_ref stays the same, not a new one).
    second = _decide_approval(kong_url, siti, approval_id, "approve")
    assert second.status_code == 200, second.text
    assert second.json()["business_ref"] == first_body["business_ref"]

    with db_conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("tnt_demo",))
        cur.execute(
            "SELECT status, business_ref FROM audit.mutation_requests WHERE approval_id = %s",
            (approval_id,),
        )
        row = cur.fetchone()
    db_conn.commit()
    assert row is not None
    assert row[0] == "executed"
    assert row[1] == first_body["business_ref"]


def test_approver_without_required_permission_is_rejected(
    kong_url: str, mint_jwt: Callable[..., str]
) -> None:
    budi = mint_jwt(
        tenant_id="tnt_demo",
        user_id="usr_budi",
        acl_group_ids=["grp_all_staff"],
        employee_id="emp_001",
        permissions=["policy.read", "leave.balance.read", "leave.request.create", "payslip.read"],
    )
    # Finance role: can submit their own leave requests but has no
    # leave.request.approve — must not be able to approve anyone else's.
    dewi = mint_jwt(
        tenant_id="tnt_demo",
        user_id="usr_dewi",
        acl_group_ids=["grp_all_staff"],
        employee_id="emp_004",
        permissions=[
            "policy.read",
            "leave.balance.read",
            "leave.request.create",
            "payslip.read",
            "payroll.read",
            "reimbursement.approve",
        ],
    )

    invoke_resp = _invoke(
        kong_url,
        budi,
        "Saya ingin mengajukan cuti selama 6 hari kerja mulai tanggal 15 Oktober 2026.",
        allow_mutations=True,
    )
    assert invoke_resp.status_code == 200, invoke_resp.text
    approval_id = invoke_resp.json()["pending_approvals"][0]["approval_id"]

    resp = _decide_approval(kong_url, dewi, approval_id, "approve")
    assert resp.status_code == 403


def test_low_risk_leave_request_executes_immediately_without_approval(
    kong_url: str, mint_jwt: Callable[..., str]
) -> None:
    token = mint_jwt(
        tenant_id="tnt_demo",
        user_id="usr_eko",
        acl_group_ids=["grp_all_staff"],
        employee_id="emp_005",
        permissions=["policy.read", "leave.balance.read", "leave.request.create", "payslip.read"],
    )
    resp = _invoke(
        kong_url,
        token,
        "Saya ingin mengajukan cuti selama 2 hari kerja mulai tanggal 3 November 2026.",
        allow_mutations=True,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pending_approvals"] == []
