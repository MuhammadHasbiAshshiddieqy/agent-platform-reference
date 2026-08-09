"""§10/§26.2 step 6's live proof of the semantic cache, end to end
through Kong. §10's five conditions and the store's own KNN/ACL/TAG
mechanics are already covered deterministically by `services/harness/
tests/unit/test_cache_eligibility.py`, `test_graph_cache.py`, and
`tests/integration/test_cache_store.py` (a real RediSearch container) —
this file is only about proving the wiring between gateway, harness, and
the live Redis instance is real, the same division of labor
`test_m5b_rbac.py`/`test_m6_async.py` document for their own milestones.

One deliberate deviation from "just call the real API and see if it
caches": this dev environment's CPU-only `agent-local` (qwen2.5:3b)
fallback has a strong, reproducible bias toward calling `get_leave_
balance` on nearly every `hr-assistant` turn regardless of the
question's actual topic (confirmed live — even a pure reimbursement-
policy question triggered it twice in three separate attempts) — see
CLAUDE.md's known quirks. Since `get_leave_balance` is `cacheable:
false`, that alone would make almost every organic run cache-ineligible
and turn this suite into a coin flip having nothing to do with the
cache code itself. So the ACL-isolation tests (§26.2 step 6d's "most
important assertion in the whole demo") seed the cache directly through
the real embedding pipeline + real `SemanticCacheStore` — exactly what
`cache_write` would have produced from a real eligible run — and then
verify hit/miss purely through live HTTP calls, which never depends on
whether the model decides to call a tool. The "never cache a personal
answer" test is the one case that's naturally robust to the model's
tool-calling bias either way (asking about leave balance should always
skip caching, whether or not the tool actually fires), so it's tested
organically.
"""

from __future__ import annotations

import struct
import time
import uuid
from collections.abc import Callable

import httpx
import pytest
import redis

HARNESS_URL = "http://localhost:8081"
MODEL_ROUTER_URL = "http://localhost:4000"
VECTOR_DIM = 1024
PROMPT_VERSION = "system_prompt@v1"


def _vector_bytes(values: list[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def _acl_hash(acl_group_ids: list[str]) -> str:
    import hashlib

    joined = ",".join(sorted(acl_group_ids))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


@pytest.fixture()
def model_router_key(env: dict[str, str]) -> str:
    return env["LITELLM_MASTER_KEY"]


def _embed(text: str, api_key: str) -> list[float]:
    resp = httpx.post(
        f"{MODEL_ROUTER_URL}/embeddings",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "embedding-default", "input": [text]},
        timeout=30.0,
    )
    assert resp.status_code == 200, resp.text
    return list(resp.json()["data"][0]["embedding"])


@pytest.fixture()
def semcache_redis() -> redis.Redis:
    # db 0 — RediSearch only indexes db 0, see cache/store.py's docstring.
    client = redis.Redis.from_url("redis://localhost:6379/0")
    # Full isolation between runs, not just between tests in one run: a
    # PRIOR successful run of this same suite (e.g. before `infinity`
    # crashed mid-run and forced a re-run) can leave a real, organically
    # -written entry behind — it has its own TTL (up to 1h) and would
    # otherwise silently satisfy a later "must miss" assertion for the
    # wrong reason (the requesting user hitting their OWN earlier answer,
    # not a cross-ACL leak). Caught exactly this way once while writing
    # this file — see CLAUDE.md's known quirks.
    for key in client.keys("semcache:tnt_demo:*"):
        client.delete(key)
    return client


def _seed_cache_entry(
    semcache_redis: redis.Redis,
    *,
    query_text: str,
    api_key: str,
    tenant_id: str,
    agent_id: str,
    acl_group_ids: list[str],
    answer: str,
    document_id: str,
) -> None:
    """Writes a cache entry through the exact fields `cache/store.py`'s
    `SemanticCacheStore.write` would, using a REAL embedding from the
    live model-router — this is the read path's fixture, not a
    shortcut around it: `test_cache_store.py` already proves `write`
    and `lookup` are the same code, so seeding by hand here and reading
    back through the real HTTP path (Kong -> gateway -> harness ->
    `cache_lookup`) still exercises the live lookup end to end.
    """
    embedding = _embed(query_text, api_key)
    key = f"semcache:{tenant_id}:{uuid.uuid4().hex[:20]}"
    semcache_redis.hset(
        key,
        mapping={
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "acl_hash": _acl_hash(acl_group_ids),
            "prompt_version": PROMPT_VERSION,
            "document_ids": document_id,
            "embedding": _vector_bytes(embedding),
            "body": (
                '{"output_text": "'
                + answer.replace('"', '\\"')
                + '", "citations": [], "document_ids": ["'
                + document_id
                + '"]}'
            ),
        },
    )
    semcache_redis.expire(key, 3600)
    # Give RediSearch a moment to index the new hash key.
    time.sleep(0.3)


def _invoke(kong_url: str, token: str, question: str) -> httpx.Response:
    return httpx.post(
        f"{kong_url}/v1/agent/invoke",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
        json={"agent_id": "hr-assistant", "input": {"type": "text", "content": question}},
        timeout=180.0,
    )


REIMBURSEMENT_QUESTION = (
    "Berapa batas maksimal klaim reimbursement operasional per bulan tanpa persetujuan tambahan?"
)
REIMBURSEMENT_ANSWER = (
    "Klaim reimbursement untuk keperluan operasional dibatasi maksimal Rp 500.000 per bulan "
    "per karyawan tanpa persetujuan tambahan."
)


def test_seeded_entry_hits_for_same_and_different_user_sharing_acl(
    kong_url: str,
    mint_jwt: Callable[..., str],
    semcache_redis: redis.Redis,
    model_router_key: str,
) -> None:
    acl = ["grp_all_staff", "grp_engineering"]
    _seed_cache_entry(
        semcache_redis,
        query_text=REIMBURSEMENT_QUESTION,
        api_key=model_router_key,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_group_ids=acl,
        answer=REIMBURSEMENT_ANSWER,
        document_id="doc_test_reimbursement",
    )

    budi = mint_jwt(user_id="usr_budi", acl_group_ids=acl)
    resp1 = _invoke(kong_url, budi, REIMBURSEMENT_QUESTION)
    assert resp1.status_code == 200, resp1.text
    body1 = resp1.json()
    assert body1["cache_hit"] is True
    assert body1["usage"]["input_tokens"] == 0
    assert body1["usage"]["output_tokens"] == 0
    assert REIMBURSEMENT_ANSWER in body1["output"]["content"]

    # A different user, identical ACL set — §26.2 step 6c: sharing
    # acl_hash is what makes this hit, not who wrote it.
    eko = mint_jwt(user_id="usr_eko", acl_group_ids=acl)
    resp2 = _invoke(kong_url, eko, REIMBURSEMENT_QUESTION)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["cache_hit"] is True


def test_seeded_entry_misses_for_a_user_with_different_acl(
    kong_url: str,
    mint_jwt: Callable[..., str],
    semcache_redis: redis.Redis,
    model_router_key: str,
) -> None:
    # §26.2 step 6d — "the most important assertion in the whole demo":
    # a cache entry built under one ACL namespace must never surface to
    # a lookup under a different one, even for the identical question.
    _seed_cache_entry(
        semcache_redis,
        query_text=REIMBURSEMENT_QUESTION,
        api_key=model_router_key,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_group_ids=["grp_all_staff", "grp_engineering"],
        answer=REIMBURSEMENT_ANSWER,
        document_id="doc_test_reimbursement",
    )

    dewi = mint_jwt(user_id="usr_dewi", acl_group_ids=["grp_all_staff", "grp_finance"])
    resp = _invoke(kong_url, dewi, REIMBURSEMENT_QUESTION)
    assert resp.status_code == 200, resp.text
    assert resp.json()["cache_hit"] is False


def test_personal_question_is_never_cache_hit_or_written(
    kong_url: str, mint_jwt: Callable[..., str], semcache_redis: redis.Redis
) -> None:
    acl = ["grp_all_staff", "grp_engineering"]
    token = mint_jwt(
        user_id="usr_budi",
        employee_id="emp_001",
        acl_group_ids=acl,
        permissions=["policy.read", "leave.balance.read", "leave.request.create", "payslip.read"],
    )
    before_keys = set(semcache_redis.keys("semcache:tnt_demo:*"))

    resp = _invoke(kong_url, token, "Berapa sisa cuti saya?")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cache_hit"] is False

    # get_leave_balance is cacheable: false — this run must never have
    # written a new entry, whether or not the model actually called it.
    after_keys = set(semcache_redis.keys("semcache:tnt_demo:*"))
    assert after_keys == before_keys


def test_cache_invalidate_endpoint_removes_matching_entries(
    semcache_redis: redis.Redis, model_router_key: str
) -> None:
    # Exercises the exact endpoint `ingestion/clients/harness.py` calls
    # after a document change (§10's invalidation rule) — called
    # directly here (harness's `/internal/v1/...` surface has no Kong
    # route, same as retrieval-service/mock-business-api's own internal
    # endpoints) rather than round-tripping through a real ingestion run,
    # which would mean mutating `seed/documents/*.md` mid-suite.
    document_id = f"doc_test_invalidate_{uuid.uuid4().hex[:8]}"
    _seed_cache_entry(
        semcache_redis,
        query_text=f"pertanyaan uji invalidasi {document_id}",
        api_key=model_router_key,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_group_ids=["grp_all_staff"],
        answer="jawaban uji",
        document_id=document_id,
    )
    before = semcache_redis.keys("semcache:tnt_demo:*")
    assert len(before) >= 1

    resp = httpx.post(
        f"{HARNESS_URL}/internal/v1/cache/invalidate",
        json={"tenant_id": "tnt_demo", "document_ids": [document_id]},
        timeout=30.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["invalidated_count"] == 1

    time.sleep(0.2)
    remaining = semcache_redis.keys("semcache:tnt_demo:*")
    assert len(remaining) == len(before) - 1
