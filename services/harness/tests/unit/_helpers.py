"""Shared test helper, not itself a test module (no `test_` prefix, so
pytest never collects it). Mirrors the `httpx.MockTransport` pattern in
test_model_router_client.py, factored out since the guardrail tests all
need to mock an `agent-cheap` classifier call the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
from contracts.common import Audience
from harness.authz.agent_profile import AgentProfile, load_agent_profiles
from harness.authz.manifest import ToolManifestEntry, load_tool_manifests
from harness.authz.policy_resolver import YamlPolicyResolver
from harness.clients.model_router import ModelRouterClient

# services/harness/tests/unit/_helpers.py -> repo root. Tests load the
# REAL config/tools, config/agents YAML — same "genuine code path, not a
# mock of it" reasoning services/mock-business-api/tests/integration/
# test_contract.py already documents for its own fixtures; PolicyResolver
# behavior is exactly what §22.9's required tests need proven for real.
REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_TOOLS_DIR = REPO_ROOT / "config" / "tools"
CONFIG_AGENTS_DIR = REPO_ROOT / "config" / "agents"


def real_tool_manifests(*, audience: Audience = Audience.INTERNAL) -> dict[str, ToolManifestEntry]:
    return load_tool_manifests(CONFIG_TOOLS_DIR, audience=audience)


def real_agent_profiles(*, audience: Audience = Audience.INTERNAL) -> dict[str, AgentProfile]:
    return load_agent_profiles(CONFIG_AGENTS_DIR, audience=audience)


class FakeKillswitchChecker:
    def __init__(
        self, *, disabled_tools: set[str] | None = None, disabled_agents: set[str] | None = None
    ) -> None:
        self._disabled_tools = disabled_tools or set()
        self._disabled_agents = disabled_agents or set()

    async def is_tool_disabled(self, tool_name: str) -> bool:
        return tool_name in self._disabled_tools

    async def is_agent_disabled(self, agent_id: str) -> bool:
        return agent_id in self._disabled_agents


def real_policy_resolver(
    *, audience: Audience = Audience.INTERNAL, killswitch: FakeKillswitchChecker | None = None
) -> YamlPolicyResolver:
    return YamlPolicyResolver(
        tool_manifests=load_tool_manifests(CONFIG_TOOLS_DIR, audience=audience),
        agent_profiles=load_agent_profiles(CONFIG_AGENTS_DIR, audience=audience),
        killswitch=killswitch or FakeKillswitchChecker(),  # type: ignore[arg-type]
    )


class FakeTokenExchangeClient:
    def __init__(self, *, token: str = "exchanged-token-test") -> None:
        self._token = token
        self.calls: list[dict[str, str]] = []

    async def exchange(self, *, subject_token: str, audience: str, scope: str) -> str:
        self.calls.append({"subject_token": subject_token, "audience": audience, "scope": scope})
        return self._token

    async def aclose(self) -> None:
        pass


def mock_model_router(handler: Callable[[httpx.Request], httpx.Response]) -> ModelRouterClient:
    client = ModelRouterClient("http://model-router:4000", "test-key")
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://model-router:4000"
    )
    return client


def text_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


class _FakeChatResult:
    def __init__(self, content: str | None, tool_calls: list[object] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.input_tokens = 1
        self.output_tokens = 1
        self.cost_usd = 0.0


class FakeModelRouter:
    """Graph-level double: distinguishes the guardrail classifier prompts
    (by a substring unique to each template) from a real "answer the
    user" call, which every other prompt falls through to. Used by
    graph/build.py tests, which exercise `respond` and `output_guardrails`'
    regenerate path in addition to the classifiers `pipeline.py`'s own
    tests already cover in isolation.
    """

    def __init__(
        self,
        *,
        injection_response: str = "0",  # "1" = flagged as injection, "0" = safe
        offtopic_response: str = "1",  # "1" = on-topic/allow, "0" = off-topic/block
        groundedness_score: str = "0.9",
        answer: str = "Sisa cuti Anda 8 hari kerja.",
        tool_calls: list[object] | None = None,  # returned on the FIRST non-classifier call only
    ) -> None:
        self.injection_response = injection_response
        self.offtopic_response = offtopic_response
        self.groundedness_score = groundedness_score
        self.answer = answer
        self._tool_calls = tool_calls
        self.calls: list[tuple[str, list[dict[str, object]]]] = []
        # Names of every tool actually offered on each chat() call, in
        # order — lets tests assert on what the model was allowed to see
        # (§22.4's filtering), not just on what it ended up doing.
        self.tools_offered: list[set[str]] = []

    async def chat(
        self,
        model_alias: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float | None = None,
        api_key: str | None = None,
    ) -> _FakeChatResult:
        self.calls.append((model_alias, messages))
        self.tools_offered.append(
            {t["function"]["name"] for t in tools} if tools else set()  # type: ignore[index]
        )
        content = messages[0]["content"]
        assert isinstance(content, str)
        if "Topik assistant ini" in content:
            return _FakeChatResult(self.offtopic_response)
        if "menilai apakah sebuah jawaban" in content:
            return _FakeChatResult(self.groundedness_score)
        if "prompt injection" in content:
            return _FakeChatResult(self.injection_response)
        if self._tool_calls and not any(m.get("role") == "tool" for m in messages):
            # Only offer tool_calls once — once a `tool` role message is
            # present, this is a follow-up call after `act` ran, and
            # should produce the final text answer instead.
            return _FakeChatResult(None, tool_calls=self._tool_calls)
        return _FakeChatResult(self.answer)

    async def aclose(self) -> None:
        pass


class FakeEmbeddingClient:
    """§10 cache lookup's embedder — a fixed canned vector by default, so
    graph-level tests that never exercise the cache path (most of them:
    `agent_profiles={}` keeps `cache_lookup` skipping before this is ever
    called) don't need a real model-router. `test_graph_cache.py` passes
    its own instance with `embed_query` overridden when it actually
    wants to drive cache hit/miss behavior.
    """

    def __init__(self, vector: list[float] | None = None) -> None:
        self._vector = vector or ([1.0] + [0.0] * 1023)
        self.calls: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return self._vector

    async def aclose(self) -> None:
        pass


class FakeCacheStore:
    """§10's `SemanticCacheStore` double — an in-memory dict, no Redis.
    Real RediSearch behavior (KNN scoring, TAG escaping, ACL isolation)
    is proven against a live container in `tests/integration/
    test_cache_store.py`; this fake is only for graph-level wiring tests
    that need to control hit/miss deterministically without embeddings
    or vector math.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str, str], object] = {}
        self.writes: list[dict[str, object]] = []

    async def lookup(
        self,
        embedding: list[float],
        *,
        tenant_id: str,
        agent_id: str,
        acl_hash: str,
        prompt_version: str,
    ) -> object | None:
        return self._entries.get((tenant_id, agent_id, acl_hash, prompt_version))

    async def write(
        self,
        embedding: list[float],
        *,
        tenant_id: str,
        agent_id: str,
        acl_hash: str,
        prompt_version: str,
        ttl_seconds: int,
        payload: object,
    ) -> None:
        self._entries[(tenant_id, agent_id, acl_hash, prompt_version)] = payload
        self.writes.append(
            {
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "acl_hash": acl_hash,
                "prompt_version": prompt_version,
                "ttl_seconds": ttl_seconds,
                "payload": payload,
            }
        )


class FakeRetrievalClient:
    def __init__(
        self, chunks: list[object] | None = None, degraded: list[str] | None = None
    ) -> None:
        self._chunks = chunks or []
        self._degraded = degraded or []

    async def search(self, request: object) -> object:
        from contracts.retrieval import SearchResult

        return SearchResult(chunks=self._chunks, degraded=self._degraded, latency_ms=1)  # type: ignore[arg-type]

    async def aclose(self) -> None:
        pass


class FakeBusinessApiClient:
    """Records every call so tests can assert on preview/execute
    sequencing without a live mock-business-api. Canned responses are
    intentionally minimal — the real contract shapes are already proven
    by services/mock-business-api/tests/integration/test_contract.py."""

    def __init__(
        self,
        *,
        query_result: dict[str, object] | None = None,
        risk_level: str = "medium",
        requires_approval: bool = False,
        business_ref: str = "lvr_test",
    ) -> None:
        self._query_result = query_result or {"employee_id": "emp_001", "leave_balance": 8}
        self._risk_level = risk_level
        self._requires_approval = requires_approval
        self._business_ref = business_ref
        self.queries: list[dict[str, object]] = []
        self.previews: list[dict[str, object]] = []
        self.executes: list[dict[str, object]] = []

    async def query(self, **kwargs: object) -> dict[str, object]:
        self.queries.append(kwargs)
        return self._query_result

    async def preview(self, **kwargs: object) -> object:
        from contracts.business_api import PreviewResponse
        from contracts.common import RiskLevel

        self.previews.append(kwargs)
        return PreviewResponse(
            action=str(kwargs["action"]),
            risk_level=RiskLevel(self._risk_level),
            requires_approval=self._requires_approval,
            effects=[],
            reversible=True,
            preview_token=f"prv_test_{len(self.previews)}",
            validation_errors=[],
        )

    async def execute(self, **kwargs: object) -> object:
        from datetime import UTC, datetime

        from contracts.business_api import ExecuteResponse

        self.executes.append(kwargs)
        return ExecuteResponse(
            action=str(kwargs["action"]),
            status="executed",
            business_ref=self._business_ref,
            executed_at=datetime.now(UTC),
        )

    async def aclose(self) -> None:
        pass
