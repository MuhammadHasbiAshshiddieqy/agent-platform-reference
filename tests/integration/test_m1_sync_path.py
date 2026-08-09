"""M1 DoD (§15): a valid JWT through Kong returns an LLM answer; the trace
in Langfuse has the same trace_id as the response; a row lands in
conversation.agent_runs; tenant isolation still holds (tests/security is
the authority on that last one — this file doesn't re-litigate RLS).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

import httpx
import psycopg
import pytest


def _headers(token: str, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _invoke_body(content: str = "Reply with exactly one word: hello.") -> dict:
    return {
        "agent_id": "m1-smoke-test",
        "input": {"type": "text", "content": content},
    }


def test_invoke_through_kong_returns_llm_answer_with_matching_trace_id(
    kong_url: str, mint_jwt: Callable[..., str]
) -> None:
    token = mint_jwt()
    idempotency_key = str(uuid.uuid4())

    resp = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_headers(token, idempotency_key),
        json=_invoke_body(),
        timeout=160.0,  # first call may cold-start the local Ollama model
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["output"]["content"]
    assert body["usage"]["output_tokens"] > 0
    # §12.1 — response header and response body must carry the identical
    # trace_id, or an incident responder can't correlate the two.
    assert resp.headers["X-Trace-Id"] == body["trace_id"]


def test_missing_jwt_is_rejected(kong_url: str) -> None:
    resp = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json=_invoke_body(),
    )
    assert resp.status_code == 401


def test_missing_idempotency_key_is_rejected(kong_url: str, mint_jwt: Callable[..., str]) -> None:
    resp = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_headers(mint_jwt()),
        json=_invoke_body(),
    )
    assert resp.status_code == 400


def test_agent_run_persisted_with_matching_trace_id(
    kong_url: str, mint_jwt: Callable[..., str], db_conn: psycopg.Connection
) -> None:
    token = mint_jwt(tenant_id="tnt_demo", user_id="usr_siti")
    idempotency_key = str(uuid.uuid4())

    resp = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_headers(token, idempotency_key),
        json=_invoke_body(),
        timeout=160.0,
    )
    assert resp.status_code == 200, resp.text
    trace_id = resp.json()["trace_id"]

    with db_conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("tnt_demo",))
        cur.execute(
            "SELECT status, output_tokens, agent_id FROM conversation.agent_runs "
            "WHERE trace_id = %s",
            (trace_id,),
        )
        row = cur.fetchone()
    db_conn.commit()

    assert row is not None, "no conversation.agent_runs row for this trace_id"
    status_, output_tokens, agent_id = row
    assert status_ == "succeeded"
    assert output_tokens > 0
    assert agent_id == "m1-smoke-test"


def test_idempotent_replay_does_not_create_a_second_run(
    kong_url: str, mint_jwt: Callable[..., str], db_conn: psycopg.Connection
) -> None:
    token = mint_jwt()
    idempotency_key = str(uuid.uuid4())
    body = _invoke_body("Reply with exactly one word: idempotent.")

    first = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_headers(token, idempotency_key),
        json=body,
        timeout=160.0,
    )
    assert first.status_code == 200, first.text

    second = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_headers(token, idempotency_key),
        json=body,
        timeout=30.0,
    )
    assert second.status_code == 200, second.text
    assert second.json() == first.json()  # identical cached response, not a fresh run

    trace_id = first.json()["trace_id"]
    with db_conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", ("tnt_demo",))
        cur.execute("SELECT count(*) FROM conversation.agent_runs WHERE trace_id = %s", (trace_id,))
        (count,) = cur.fetchone()
    db_conn.commit()
    assert count == 1


def test_reusing_idempotency_key_with_different_body_is_rejected(
    kong_url: str, mint_jwt: Callable[..., str]
) -> None:
    token = mint_jwt()
    idempotency_key = str(uuid.uuid4())

    first = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_headers(token, idempotency_key),
        json=_invoke_body("first body"),
        timeout=160.0,
    )
    assert first.status_code == 200, first.text

    second = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_headers(token, idempotency_key),
        json=_invoke_body("a completely different body"),
        timeout=30.0,
    )
    assert second.status_code == 409


def test_trace_appears_in_langfuse_with_matching_id(
    kong_url: str, langfuse_url: str, mint_jwt: Callable[..., str], env: dict[str, str]
) -> None:
    token = mint_jwt()
    idempotency_key = str(uuid.uuid4())

    resp = httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers=_headers(token, idempotency_key),
        json=_invoke_body("Reply with exactly one word: traced."),
        timeout=160.0,
    )
    assert resp.status_code == 200, resp.text
    trace_id = resp.json()["trace_id"]

    auth = (env["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"], env["LANGFUSE_INIT_PROJECT_SECRET_KEY"])
    # harness calls langfuse.flush() before returning, but Langfuse's own
    # ingestion pipeline (queue -> ClickHouse) is async server-side — poll
    # rather than assume it's queryable the instant the HTTP call returns.
    deadline = time.monotonic() + 30
    last_status = None
    while time.monotonic() < deadline:
        trace_resp = httpx.get(f"{langfuse_url}/api/public/traces/{trace_id}", auth=auth)
        last_status = trace_resp.status_code
        if trace_resp.status_code == 200:
            assert trace_resp.json()["id"] == trace_id
            return
        time.sleep(1.5)

    pytest.fail(f"trace {trace_id} never appeared in Langfuse (last status: {last_status})")
