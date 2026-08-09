"""M3 DoD (§15/§26.2): a question answerable from an ingested document comes
back with a valid citation through the *full* live path — Kong -> gateway
-> harness -> retrieval-service -> model-router — and ACL enforcement
(catalog.chunks.acl_group_ids && JWT's acl_group_ids) holds end to end, not
just at the isolated hybrid-search-query level already proven by
services/retrieval/tests/integration/test_hybrid_search.py.

Requires the seed corpus (seed/documents/*.md) already ingested into
tenant tnt_demo — `make up` + `curl -X POST
http://localhost:8083/internal/v1/ingest/tnt_demo` (see README.md M3
quickstart). Ingestion idempotency (0 upsert on re-run) is proven directly
against ingestion-service, not through Kong, since ingestion has no public
gateway route (§8 — it's an internal/operational service).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import pytest

INGESTION_URL = "http://localhost:8083"


def _invoke_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}


def _invoke(kong_url: str, token: str, question: str) -> httpx.Response:
    return httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_invoke_headers(token),
        json={"agent_id": "m3-rag-test", "input": {"type": "text", "content": question}},
        timeout=160.0,
    )


def _source_uris(resp_json: dict) -> set[str]:
    return {c["source_uri"] for c in resp_json["output"]["citations"]}


def test_question_answerable_from_seed_doc_gets_valid_citation(
    kong_url: str, mint_jwt: Callable[..., str]
) -> None:
    token = mint_jwt(tenant_id="tnt_demo", acl_group_ids=["grp_all_staff"])
    resp = _invoke(
        kong_url,
        token,
        "Berapa hari sebelumnya karyawan wajib memberi pemberitahuan untuk cuti panjang?",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "retrieval" not in body["degraded"]
    citations = body["output"]["citations"]
    assert citations, "expected at least one citation"
    assert "file://kebijakan-cuti-2026.md" in _source_uris(body)
    for c in citations:
        assert c["document_id"]
        assert c["chunk_id"]
        assert 0.0 <= c["score"] <= 1.0


def test_employee_without_hr_acl_gets_no_payroll_sop_citation(
    kong_url: str, mint_jwt: Callable[..., str]
) -> None:
    token = mint_jwt(tenant_id="tnt_demo", acl_group_ids=["grp_all_staff"])
    resp = _invoke(kong_url, token, "Apa prosedur SOP-PR-014 untuk penyesuaian payroll?")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # §26.2 demo step 2 — an employee-role user must never see the
    # HR-only document surface, even as a citation on an unrelated answer.
    assert "file://sop-penyesuaian-payroll.md" not in _source_uris(body)


def test_hr_manager_with_hr_acl_gets_payroll_sop_citation(
    kong_url: str, mint_jwt: Callable[..., str]
) -> None:
    token = mint_jwt(
        tenant_id="tnt_demo", user_id="usr_hr_manager", acl_group_ids=["grp_all_staff", "grp_hr"]
    )
    resp = _invoke(kong_url, token, "Apa prosedur SOP-PR-014 untuk penyesuaian payroll?")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # §26.2 demo step 3 — the identical question, asked by a user whose
    # ACL grant includes grp_hr, must surface the document that step 2
    # proved was invisible without it. Same code path, different JWT
    # claim, opposite outcome — that's the ACL enforcement claim.
    assert "file://sop-penyesuaian-payroll.md" in _source_uris(body)


@pytest.mark.parametrize("_iteration", [1, 2])
def test_reingestion_is_idempotent_zero_upsert_on_unchanged_content(_iteration: int) -> None:
    resp = httpx.post(f"{INGESTION_URL}/internal/v1/ingest/tnt_demo", timeout=60.0)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["docs_seen"] == 5
    assert body["errors"] == 0
    if _iteration == 2:
        # First iteration may or may not upsert depending on whether a
        # prior manual run already synced the corpus; the second run
        # against now-unchanged content_hash must upsert nothing.
        assert body["docs_upserted"] == 0
        assert body["docs_deleted"] == 0
