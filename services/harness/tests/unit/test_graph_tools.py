"""Graph-level wiring proof for §5.3's `act` and §22's `authorize`:
`respond` <-> `act` round-trips correctly, `MAX_TOOL_ITERATIONS` actually
bounds the loop, a mutation that escalates to `high` risk surfaces a
`pending_approval` on the final state instead of executing, and §22.4's
tool-visibility filter actually keeps a disallowed tool out of the
schema. Individual tool-execution branching is covered in isolation by
test_tool_executor.py; §22.1's intersection logic itself is covered by
test_yaml_policy_resolver.py — this file is about the graph actually driving
both through `authorize`/`respond`/`act`'s edges with the right data.
Uses the REAL `config/tools`/`config/agents` YAML via `_helpers.py`'s
`real_policy_resolver`/`real_tool_manifests`, not a fake resolver.
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
    real_policy_resolver,
    real_tool_manifests,
)
from contracts.common import Audience
from harness.clients.model_router import ChatCompletionResult, ToolCallResult
from harness.graph.build import MAX_TOOL_ITERATIONS, build_graph
from harness.graph.state import AgentState

TOOL_MANIFESTS = real_tool_manifests()

# hr-assistant's designed ceiling (config/agents/hr-assistant.yaml) is all
# three internal tools; a real "employee" actor per seed/users.yaml.
EMPLOYEE_PERMISSIONS = ["policy.read", "leave.balance.read", "leave.request.create", "payslip.read"]


class _AlwaysToolCallingModelRouter:
    """Simulates the weak-local-model behavior found via live manual
    testing (see injection.py/offtopic.py docstrings for the same class
    of finding): re-requests the same tool even after receiving its
    result. `MAX_TOOL_ITERATIONS` is what has to stop this, not the
    model's own judgment."""

    def __init__(self, tool_call: ToolCallResult) -> None:
        self._tool_call = tool_call
        self.call_count = 0

    async def chat(
        self,
        model_alias: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        temperature: float | None = None,
        api_key: str | None = None,
    ) -> ChatCompletionResult:
        self.call_count += 1
        if tools:
            return ChatCompletionResult(
                content=None,
                tool_calls=[self._tool_call],
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
            )
        return ChatCompletionResult(
            content="Maaf, saya tidak dapat menyelesaikan permintaan ini.",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
        )

    async def aclose(self) -> None:
        pass


def _initial_state(
    input_text: str, *, allow_mutations: bool = False, permissions: list[str] | None = None
) -> AgentState:
    return AgentState(
        trace_id="trc_test",
        tenant_id="tnt_demo",
        user_id="usr_budi",
        agent_id="hr-assistant",
        employee_id="emp_001",
        permissions=permissions if permissions is not None else EMPLOYEE_PERMISSIONS,
        allow_mutations=allow_mutations,
        input_text=input_text,
    )


def _build(router: object) -> object:
    return build_graph(
        router,  # type: ignore[arg-type]
        FakeRetrievalClient(),  # type: ignore[arg-type]
        FakeBusinessApiClient(),  # type: ignore[arg-type]
        real_policy_resolver(),
        TOOL_MANIFESTS,
        FakeTokenExchangeClient(),  # type: ignore[arg-type]
        Audience.INTERNAL,
        FakeEmbeddingClient(),  # type: ignore[arg-type]
        FakeCacheStore(),  # type: ignore[arg-type]
        {},
    ).compile()


@pytest.mark.asyncio
async def test_tool_call_result_is_fed_back_and_produces_a_final_answer() -> None:
    tool_call = ToolCallResult(id="call_1", name="get_leave_balance", arguments={})
    router = FakeModelRouter(tool_calls=[tool_call], answer="Sisa cuti Anda 8 hari.")
    business_api = FakeBusinessApiClient(
        query_result={"employee_id": "emp_001", "leave_balance": 8}
    )
    graph = build_graph(
        router,  # type: ignore[arg-type]
        FakeRetrievalClient(),  # type: ignore[arg-type]
        business_api,  # type: ignore[arg-type]
        real_policy_resolver(),
        TOOL_MANIFESTS,
        FakeTokenExchangeClient(),  # type: ignore[arg-type]
        Audience.INTERNAL,
        FakeEmbeddingClient(),  # type: ignore[arg-type]
        FakeCacheStore(),  # type: ignore[arg-type]
        {},
    ).compile()

    result = await graph.ainvoke(_initial_state("Berapa sisa cuti saya?"))

    assert result["output_text"] == "Sisa cuti Anda 8 hari."
    assert result["pending_tool_calls"] == []
    assert len(result["tool_invocation_records"]) == 1
    assert result["tool_invocation_records"][0].tool_name == "get_leave_balance"
    # self-scope default-filled
    assert business_api.queries[0]["params"]["employee_id"] == "emp_001"


@pytest.mark.asyncio
async def test_high_risk_mutation_surfaces_pending_approval_and_does_not_execute() -> None:
    tool_call = ToolCallResult(
        id="call_1",
        name="submit_leave_request",
        arguments={"employee_id": "emp_001", "leave_days": 7, "start_date": "2026-09-01"},
    )
    router = FakeModelRouter(
        tool_calls=[tool_call], answer="Permintaan cuti Anda menunggu persetujuan."
    )
    business_api = FakeBusinessApiClient(risk_level="high", requires_approval=True)
    graph = build_graph(
        router,  # type: ignore[arg-type]
        FakeRetrievalClient(),  # type: ignore[arg-type]
        business_api,  # type: ignore[arg-type]
        real_policy_resolver(),
        TOOL_MANIFESTS,
        FakeTokenExchangeClient(),  # type: ignore[arg-type]
        Audience.INTERNAL,
        FakeEmbeddingClient(),  # type: ignore[arg-type]
        FakeCacheStore(),  # type: ignore[arg-type]
        {},
    ).compile()

    result = await graph.ainvoke(
        _initial_state("Ajukan cuti 7 hari mulai 1 September", allow_mutations=True)
    )

    assert len(result["pending_approvals"]) == 1
    assert result["pending_approvals"][0].risk_level == "high"
    assert result["mutations_previewed"] == ["submit_leave_request"]
    assert result["mutations_executed"] == []
    assert business_api.executes == []
    assert len(result["mutation_request_records"]) == 1
    assert result["mutation_request_records"][0].status == "awaiting_approval"


@pytest.mark.asyncio
async def test_medium_risk_mutation_executes_immediately_when_allowed() -> None:
    tool_call = ToolCallResult(
        id="call_1",
        name="submit_leave_request",
        arguments={"employee_id": "emp_001", "leave_days": 2, "start_date": "2026-09-01"},
    )
    router = FakeModelRouter(tool_calls=[tool_call], answer="Cuti Anda telah diajukan.")
    business_api = FakeBusinessApiClient(
        risk_level="medium", requires_approval=False, business_ref="lvr_abc"
    )
    graph = build_graph(
        router,  # type: ignore[arg-type]
        FakeRetrievalClient(),  # type: ignore[arg-type]
        business_api,  # type: ignore[arg-type]
        real_policy_resolver(),
        TOOL_MANIFESTS,
        FakeTokenExchangeClient(),  # type: ignore[arg-type]
        Audience.INTERNAL,
        FakeEmbeddingClient(),  # type: ignore[arg-type]
        FakeCacheStore(),  # type: ignore[arg-type]
        {},
    ).compile()

    result = await graph.ainvoke(
        _initial_state("Ajukan cuti 2 hari mulai 1 September", allow_mutations=True)
    )

    assert result["mutations_executed"] == ["submit_leave_request"]
    assert result["pending_approvals"] == []
    assert len(business_api.executes) == 1


@pytest.mark.asyncio
async def test_mutation_tool_is_not_offered_when_allow_mutations_is_false() -> None:
    # §22.4: a tool the caller isn't allowed to use must never even
    # appear in the schema sent to the model, not just be rejected if
    # called.
    router = FakeModelRouter(answer="Maaf, saya tidak bisa membantu itu.")
    business_api = FakeBusinessApiClient()
    graph = build_graph(
        router,  # type: ignore[arg-type]
        FakeRetrievalClient(),  # type: ignore[arg-type]
        business_api,  # type: ignore[arg-type]
        real_policy_resolver(),
        TOOL_MANIFESTS,
        FakeTokenExchangeClient(),  # type: ignore[arg-type]
        Audience.INTERNAL,
        FakeEmbeddingClient(),  # type: ignore[arg-type]
        FakeCacheStore(),  # type: ignore[arg-type]
        {},
    ).compile()

    await graph.ainvoke(_initial_state("Ajukan cuti 2 hari", allow_mutations=False))

    offered_anywhere: set[str] = (
        set().union(*router.tools_offered) if router.tools_offered else set()
    )
    assert "submit_leave_request" not in offered_anywhere
    assert "adjust_payroll" not in offered_anywhere
    assert "get_leave_balance" in offered_anywhere  # readonly tools always offered
    assert business_api.previews == []
    assert business_api.executes == []


@pytest.mark.asyncio
async def test_tool_outside_agent_profile_is_never_offered_regardless_of_permissions() -> None:
    # §22.1: agent_profile.allowed_tools is a hard ceiling — even a user
    # with every permission in the world can't get a tool hr-assistant's
    # profile never listed. search_public_faq isn't in hr-assistant.yaml.
    router = FakeModelRouter(answer="Maaf, saya tidak bisa membantu itu.")
    graph = _build(router)

    permissions = [*EMPLOYEE_PERMISSIONS, "payroll.adjust"]
    await graph.ainvoke(
        _initial_state("Cari FAQ publik", allow_mutations=True, permissions=permissions)
    )

    offered_anywhere: set[str] = (
        set().union(*router.tools_offered) if router.tools_offered else set()  # type: ignore[attr-defined]
    )
    assert "search_public_faq" not in offered_anywhere


@pytest.mark.asyncio
async def test_max_tool_iterations_bounds_a_model_that_keeps_re_requesting_the_same_tool() -> None:
    tool_call = ToolCallResult(id="call_1", name="get_leave_balance", arguments={})
    router = _AlwaysToolCallingModelRouter(tool_call)
    business_api = FakeBusinessApiClient(
        query_result={"employee_id": "emp_001", "leave_balance": 8}
    )
    graph = build_graph(
        router,  # type: ignore[arg-type]
        FakeRetrievalClient(),  # type: ignore[arg-type]
        business_api,  # type: ignore[arg-type]
        real_policy_resolver(),
        TOOL_MANIFESTS,
        FakeTokenExchangeClient(),  # type: ignore[arg-type]
        Audience.INTERNAL,
        FakeEmbeddingClient(),  # type: ignore[arg-type]
        FakeCacheStore(),  # type: ignore[arg-type]
        {},
    ).compile()

    result = await graph.ainvoke(_initial_state("Berapa sisa cuti saya?"))

    # Bounded, not infinite: exactly MAX_TOOL_ITERATIONS act() runs, then
    # one final respond() call with no tools offered forces text.
    assert result["tool_iterations"] == MAX_TOOL_ITERATIONS
    assert len(business_api.queries) == MAX_TOOL_ITERATIONS
    assert result["output_text"] == "Maaf, saya tidak dapat menyelesaikan permintaan ini."
    assert result["pending_tool_calls"] == []
