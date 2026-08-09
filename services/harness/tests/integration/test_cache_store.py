"""§10's `SemanticCacheStore` against a REAL `redis/redis-stack-server`
container — testcontainers, same "genuine code path, not a mock of it"
reasoning as `services/retrieval/tests/integration/test_hybrid_search.py`'s
own pgvector container. RediSearch's KNN/TAG-filter behavior (in
particular: COSINE metric returns *distance*, not similarity, and TAG
values like `hr-assistant`/`system_prompt@v1` need escaping) isn't
something a mock could prove — this file is what actually caught both of
those before `cache/store.py` was written, not after.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import pytest_asyncio
from harness.cache.store import CachedPayload, SemanticCacheStore
from redis.asyncio import Redis as AsyncRedis
from testcontainers.community.redis import RedisContainer

VECTOR_DIM = 8


@pytest.fixture(scope="module")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer(image="redis/redis-stack-server:latest") as container:
        yield container


@pytest_asyncio.fixture()
async def store(redis_container: RedisContainer) -> SemanticCacheStore:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    redis = AsyncRedis.from_url(f"redis://{host}:{port}/0")
    cache = SemanticCacheStore(redis, vector_dim=VECTOR_DIM, similarity_threshold=0.95)
    await cache.ensure_index()
    # Each test gets a clean slate — FLUSHDB is safe here, this
    # container is single-purpose and module-scoped for speed, not
    # shared state across tests that expect isolation.
    await redis.flushdb()
    await cache.ensure_index()
    return cache


def _payload(text: str = "14 hari kerja") -> CachedPayload:
    return CachedPayload(output_text=text, citations=[], document_ids=["doc_kebijakan_cuti"])


@pytest.mark.asyncio
async def test_lookup_misses_on_empty_index(store: SemanticCacheStore) -> None:
    result = await store.lookup(
        [1.0] + [0.0] * (VECTOR_DIM - 1),
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_write_then_lookup_with_near_identical_vector_hits(
    store: SemanticCacheStore,
) -> None:
    base = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    await store.write(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
        ttl_seconds=3600,
        payload=_payload(),
    )

    near_identical = [0.995, 0.03, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    hit = await store.lookup(
        near_identical,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
    )
    assert hit is not None
    assert hit.output_text == "14 hari kerja"
    assert hit.document_ids == ["doc_kebijakan_cuti"]


@pytest.mark.asyncio
async def test_lookup_misses_when_below_similarity_threshold(store: SemanticCacheStore) -> None:
    base = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    await store.write(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
        ttl_seconds=3600,
        payload=_payload(),
    )

    # Same direction but not close enough (§10: 0.95 is deliberately
    # conservative) — a paraphrase-but-different-topic query must miss.
    somewhat_similar = [0.7, 0.7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    hit = await store.lookup(
        somewhat_similar,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
    )
    assert hit is None


@pytest.mark.asyncio
async def test_lookup_misses_across_different_acl_hash(store: SemanticCacheStore) -> None:
    # §26.2 step 6d's "most important assertion in the whole demo" — a
    # cached answer built under one ACL namespace must never surface to
    # a lookup under a different one, even with an identical vector.
    base = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    await store.write(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="acl_engineering",
        prompt_version="system_prompt@v1",
        ttl_seconds=3600,
        payload=_payload(),
    )

    hit = await store.lookup(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="acl_finance",
        prompt_version="system_prompt@v1",
    )
    assert hit is None


@pytest.mark.asyncio
async def test_lookup_hits_across_different_users_sharing_acl_hash(
    store: SemanticCacheStore,
) -> None:
    # The other half of step 6c/6d: identical acl_hash must still hit
    # regardless of which user wrote vs. reads the entry — the namespace
    # never includes user_id (namespace.py's docstring).
    base = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    await store.write(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="shared_acl",
        prompt_version="system_prompt@v1",
        ttl_seconds=3600,
        payload=_payload(),
    )
    hit = await store.lookup(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="shared_acl",
        prompt_version="system_prompt@v1",
    )
    assert hit is not None


@pytest.mark.asyncio
async def test_lookup_misses_across_different_prompt_version(store: SemanticCacheStore) -> None:
    # §10: bumping prompt_version makes old entries unreachable, even
    # with a special character (`@`) in the tag value that needs
    # RediSearch escaping — this is the exact case that motivated
    # store.py's `_escape_tag` helper.
    base = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    await store.write(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
        ttl_seconds=3600,
        payload=_payload(),
    )
    hit = await store.lookup(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v2",
    )
    assert hit is None


@pytest.mark.asyncio
async def test_invalidate_by_documents_removes_matching_entries_only(
    store: SemanticCacheStore,
) -> None:
    base = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    other = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    await store.write(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
        ttl_seconds=3600,
        payload=CachedPayload(output_text="a", citations=[], document_ids=["doc_1", "doc_2"]),
    )
    await store.write(
        other,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
        ttl_seconds=3600,
        payload=CachedPayload(output_text="b", citations=[], document_ids=["doc_3"]),
    )

    count = await store.invalidate_by_documents(tenant_id="tnt_demo", document_ids=["doc_1"])
    assert count == 1

    # The entry citing doc_1 is gone...
    hit_a = await store.lookup(
        base,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
    )
    assert hit_a is None
    # ...but the unrelated entry (doc_3) survives.
    hit_b = await store.lookup(
        other,
        tenant_id="tnt_demo",
        agent_id="hr-assistant",
        acl_hash="abc123",
        prompt_version="system_prompt@v1",
    )
    assert hit_b is not None
    assert hit_b.output_text == "b"


@pytest.mark.asyncio
async def test_ensure_index_is_idempotent(store: SemanticCacheStore) -> None:
    # Called again at every harness boot — must not raise on an index
    # that's already there.
    await store.ensure_index()
    await store.ensure_index()
