"""§10's five cache-write conditions, tested directly against
`is_cacheable` — no graph, no I/O. Graph-level wiring (does `cache_write`
actually call this and skip the store write when it returns False) is
covered by `test_graph_cache.py`; this file is only about the boolean
logic itself, one condition at a time.
"""

from __future__ import annotations

from contracts.common import Audience, DataScope, RiskLevel, ToolKind
from harness.authz.agent_profile import AgentModelConfig, AgentProfile
from harness.authz.manifest import ToolManifestEntry
from harness.cache.eligibility import is_cacheable
from harness.graph.state import AgentState
from harness.guardrails.events import GuardrailEvent
from harness.tools.records import ToolInvocationRecord

AGENT_ID = "hr-assistant"


def _profile(*, cacheable: bool = True) -> AgentProfile:
    return AgentProfile(
        agent_id=AGENT_ID,
        audience=Audience.INTERNAL,
        version=1,
        model=AgentModelConfig(
            sync="agent-primary", async_="agent-primary", classifier="agent-cheap"
        ),
        cacheable=cacheable,
    )


def _tool_manifest(name: str, *, cacheable: bool) -> ToolManifestEntry:
    return ToolManifestEntry(
        name=name,
        version=1,
        kind=ToolKind.READONLY,
        audience=[Audience.INTERNAL],
        risk_level=RiskLevel.LOW,
        description_for_model="test tool",
        parameters_schema="GetLeaveBalanceParams",
        domain="hr",
        business_action=name,
        required_permissions=[],
        data_scope=DataScope.SELF,
        cacheable=cacheable,
    )


def _state(**overrides: object) -> AgentState:
    defaults: dict[str, object] = dict(
        trace_id="trc_test",
        tenant_id="tnt_demo",
        user_id="usr_budi",
        agent_id=AGENT_ID,
        input_text="berapa lama masa pemberitahuan cuti panjang",
    )
    defaults.update(overrides)
    return AgentState(**defaults)  # type: ignore[arg-type]


def test_cacheable_when_every_condition_holds() -> None:
    state = _state()
    assert is_cacheable(state, agent_profiles={AGENT_ID: _profile()}, tool_manifests={}) is True


def test_not_cacheable_when_agent_profile_cacheable_is_false() -> None:
    state = _state()
    profiles = {AGENT_ID: _profile(cacheable=False)}
    assert is_cacheable(state, agent_profiles=profiles, tool_manifests={}) is False


def test_not_cacheable_for_unregistered_agent_id() -> None:
    state = _state(agent_id="some-unknown-agent")
    assert is_cacheable(state, agent_profiles={AGENT_ID: _profile()}, tool_manifests={}) is False


def test_not_cacheable_when_refused() -> None:
    state = _state(refused=True)
    assert is_cacheable(state, agent_profiles={AGENT_ID: _profile()}, tool_manifests={}) is False


def test_not_cacheable_when_retrieval_degraded() -> None:
    state = _state(degraded=["retrieval"])
    assert is_cacheable(state, agent_profiles={AGENT_ID: _profile()}, tool_manifests={}) is False


def test_not_cacheable_when_a_guardrail_flag_fired() -> None:
    state = _state(
        guardrail_events=[
            GuardrailEvent(
                stage="output", rule_id="groundedness", severity="info", action_taken="flag"
            )
        ]
    )
    assert is_cacheable(state, agent_profiles={AGENT_ID: _profile()}, tool_manifests={}) is False


def test_not_cacheable_when_input_pii_was_redacted() -> None:
    state = _state(
        guardrail_events=[
            GuardrailEvent(
                stage="input", rule_id="pii_redaction", severity="info", action_taken="redact"
            )
        ]
    )
    assert is_cacheable(state, agent_profiles={AGENT_ID: _profile()}, tool_manifests={}) is False


def test_not_cacheable_when_output_pii_leaked() -> None:
    state = _state(
        guardrail_events=[
            GuardrailEvent(
                stage="output", rule_id="pii_leakage", severity="warning", action_taken="redact"
            )
        ]
    )
    assert is_cacheable(state, agent_profiles={AGENT_ID: _profile()}, tool_manifests={}) is False


def test_cacheable_when_only_cacheable_tools_were_called() -> None:
    state = _state(
        tool_invocation_records=[
            ToolInvocationRecord(
                tool_name="search_public_faq",
                tool_kind="readonly",
                arguments={},
                result_summary=None,
                status="ok",
            )
        ]
    )
    manifests = {"search_public_faq": _tool_manifest("search_public_faq", cacheable=True)}
    profiles = {AGENT_ID: _profile()}
    assert is_cacheable(state, agent_profiles=profiles, tool_manifests=manifests) is True


def test_not_cacheable_when_a_personal_readonly_tool_was_called() -> None:
    # get_leave_balance's real manifest is cacheable: false (§10's own
    # worked example) — modeled here with a minimal fixture rather than
    # loading config/tools/*.yaml, since this file is testing the
    # eligibility function's logic, not the real manifest content.
    state = _state(
        tool_invocation_records=[
            ToolInvocationRecord(
                tool_name="get_leave_balance",
                tool_kind="readonly",
                arguments={},
                result_summary=None,
                status="ok",
            )
        ]
    )
    manifests = {"get_leave_balance": _tool_manifest("get_leave_balance", cacheable=False)}
    profiles = {AGENT_ID: _profile()}
    assert is_cacheable(state, agent_profiles=profiles, tool_manifests=manifests) is False


def test_not_cacheable_when_a_mutation_tool_was_called() -> None:
    state = _state(
        tool_invocation_records=[
            ToolInvocationRecord(
                tool_name="submit_leave_request",
                tool_kind="mutation",
                arguments={},
                result_summary=None,
                status="ok",
            )
        ]
    )
    manifests = {"submit_leave_request": _tool_manifest("submit_leave_request", cacheable=False)}
    profiles = {AGENT_ID: _profile()}
    assert is_cacheable(state, agent_profiles=profiles, tool_manifests=manifests) is False
