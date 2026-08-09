"""§10's graph wiring: `cache_lookup` short-circuits straight to END on a
hit (never calls `respond`), a miss falls through to a normal run and
then `cache_write` persists it — but only when `cache/eligibility.py`'s
conditions hold. Uses the REAL `config/agents`/`config/tools` YAML
(`hr-assistant` is `cacheable: true`, `get_leave_balance` is
`cacheable: false`) via `_helpers.py`'s `real_agent_profiles`/
`real_tool_manifests`, same "genuine code path" reasoning as
`test_graph_tools.py`. `FakeCacheStore`/`FakeEmbeddingClient` stand in
for Redis/model-router so hit/miss is controlled deterministically —
RediSearch's own real behavior (KNN scoring, ACL isolation, TAG
escaping) is proven separately against a live container in
`tests/integration/test_cache_store.py`.
"""

from __future__ import annotations

import pytest
from _helpers import (
    FakeBusinessApiClient,
    FakeCacheStore,
    FakeEmbeddingClient,
    FakeModelRouter,
    FakeRetrievalClient,
    FakeTokenExchangeClient,
    real_agent_profiles,
    real_policy_resolver,
    real_tool_manifests,
)
from contracts.agent import Citation
from contracts.common import Audience
from contracts.retrieval import RetrievedChunk
from harness.cache.namespace import acl_hash
from harness.cache.store import CachedPayload
from harness.clients.model_router import ToolCallResult
from harness.graph.build import PROMPT_VERSION, build_graph
from harness.graph.state import AgentState

TOOL_MANIFESTS = real_tool_manifests()
AGENT_PROFILES = real_agent_profiles()
EMPLOYEE_PERMISSIONS = ["policy.read", "leave.balance.read", "leave.request.create", "payslip.read"]


def _initial_state(input_text: str, *, acl_group_ids: list[str] | None = None) -> AgentState:
    return AgentState(
        trace_id="trc_test",
        tenant_id="tnt_demo",
        user_id="usr_budi",
        agent_id="hr-assistant",
        employee_id="emp_001",
        permissions=EMPLOYEE_PERMISSIONS,
        acl_group_ids=acl_group_ids or ["grp_all_staff"],
        input_text=input_text,
    )


def _build(
    router: object,
    *,
    embedding_client: FakeEmbeddingClient,
    cache_store: FakeCacheStore,
    retrieval: object | None = None,
    business_api: object | None = None,
) -> object:
    return build_graph(
        router,  # type: ignore[arg-type]
        retrieval or FakeRetrievalClient(),  # type: ignore[arg-type]
        business_api or FakeBusinessApiClient(),  # type: ignore[arg-type]
        real_policy_resolver(),
        TOOL_MANIFESTS,
        FakeTokenExchangeClient(),  # type: ignore[arg-type]
        Audience.INTERNAL,
        embedding_client,  # type: ignore[arg-type]
        cache_store,  # type: ignore[arg-type]
        AGENT_PROFILES,
    ).compile()


@pytest.mark.asyncio
async def test_cache_miss_runs_normally_and_then_writes_to_cache() -> None:
    router = FakeModelRouter(answer="Masa pemberitahuan cuti panjang adalah 14 hari kerja.")
    embedding_client = FakeEmbeddingClient()
    cache_store = FakeCacheStore()
    chunk = RetrievedChunk(
        chunk_id="chk_1",
        document_id="doc_kebijakan_cuti",
        content="masa pemberitahuan 14 hari kerja",
        score=0.9,
        source_uri="file://kebijakan-cuti-2026.md",
    )
    graph = _build(
        router,
        embedding_client=embedding_client,
        cache_store=cache_store,
        retrieval=FakeRetrievalClient(chunks=[chunk]),
    )

    result = await graph.ainvoke(_initial_state("berapa lama masa pemberitahuan cuti panjang"))

    assert result["cache_hit"] is False
    assert result["output_text"] == "Masa pemberitahuan cuti panjang adalah 14 hari kerja."
    assert len(router.calls) >= 1  # respond actually ran
    assert len(cache_store.writes) == 1
    write = cache_store.writes[0]
    assert write["tenant_id"] == "tnt_demo"
    assert write["agent_id"] == "hr-assistant"
    assert write["acl_hash"] == acl_hash(["grp_all_staff"])
    assert write["prompt_version"] == PROMPT_VERSION


@pytest.mark.asyncio
async def test_cache_hit_skips_respond_entirely_and_returns_cached_answer() -> None:
    router = FakeModelRouter(answer="this should never be returned")
    embedding_client = FakeEmbeddingClient()
    cache_store = FakeCacheStore()
    # Pre-seed the cache under the exact namespace this run will look up.
    cached = CachedPayload(
        output_text="Masa pemberitahuan cuti panjang adalah 14 hari kerja.",
        citations=[
            Citation(
                document_id="doc_kebijakan_cuti",
                chunk_id="chk_1",
                source_uri="file://kebijakan-cuti-2026.md",
                score=0.9,
            )
        ],
        document_ids=["doc_kebijakan_cuti"],
    )
    namespace_key = ("tnt_demo", "hr-assistant", acl_hash(["grp_all_staff"]), PROMPT_VERSION)
    cache_store._entries[namespace_key] = cached
    graph = _build(router, embedding_client=embedding_client, cache_store=cache_store)

    result = await graph.ainvoke(_initial_state("berapa lama masa pemberitahuan cuti panjang"))

    assert result["cache_hit"] is True
    assert result["output_text"] == "Masa pemberitahuan cuti panjang adalah 14 hari kerja."
    assert len(result["citations"]) == 1
    assert result["citations"][0].document_id == "doc_kebijakan_cuti"
    # The whole point of a hit: no *generation* call — `cache_lookup`
    # runs after `input_guardrails` (its own classifier calls are the 2
    # entries here), never reaching `respond`'s real answer call.
    assert len(router.calls) == 2
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0
    assert result["cost_usd"] == 0.0
    # A hit never re-writes what it just read.
    assert cache_store.writes == []


@pytest.mark.asyncio
async def test_run_that_calls_a_personal_readonly_tool_is_never_written_to_cache() -> None:
    tool_call = ToolCallResult(id="call_1", name="get_leave_balance", arguments={})
    router = FakeModelRouter(tool_calls=[tool_call], answer="Sisa cuti Anda 8 hari.")
    embedding_client = FakeEmbeddingClient()
    cache_store = FakeCacheStore()
    business_api = FakeBusinessApiClient(
        query_result={"employee_id": "emp_001", "leave_balance": 8}
    )
    graph = _build(
        router,
        embedding_client=embedding_client,
        cache_store=cache_store,
        business_api=business_api,
    )

    result = await graph.ainvoke(_initial_state("berapa sisa cuti saya"))

    assert result["cache_hit"] is False
    assert result["output_text"] == "Sisa cuti Anda 8 hari."
    # get_leave_balance is cacheable: false (§10's own worked example) —
    # the eligibility gate must reject the write even though the lookup
    # itself was a plain miss.
    assert cache_store.writes == []


@pytest.mark.asyncio
async def test_agent_not_marked_cacheable_never_even_attempts_a_lookup() -> None:
    router = FakeModelRouter(answer="hello")
    embedding_client = FakeEmbeddingClient()
    cache_store = FakeCacheStore()
    graph = build_graph(
        router,  # type: ignore[arg-type]
        FakeRetrievalClient(),  # type: ignore[arg-type]
        FakeBusinessApiClient(),  # type: ignore[arg-type]
        real_policy_resolver(),
        TOOL_MANIFESTS,
        FakeTokenExchangeClient(),  # type: ignore[arg-type]
        Audience.INTERNAL,
        embedding_client,  # type: ignore[arg-type]
        cache_store,  # type: ignore[arg-type]
        {},  # no agent profiles registered at all
    ).compile()

    result = await graph.ainvoke(_initial_state("halo"))

    assert result["cache_hit"] is False
    assert embedding_client.calls == []  # never even embedded the query
    assert cache_store.writes == []
