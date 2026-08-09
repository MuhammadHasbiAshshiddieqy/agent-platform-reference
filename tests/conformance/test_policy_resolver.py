"""ADR-008 (§28.10, §22.10): the in-process `YamlPolicyResolver` sits
behind the `contracts.authz.PolicyResolver` Protocol specifically so a
future OPA/Cedar implementation is a drop-in replacement. This suite
tests the INTERFACE's observable contract, not `YamlPolicyResolver`'s
internals — a future resolver implementation must pass this exact file
unmodified (only `RESOLVER_FACTORIES` grows) to be considered a legal
substitute. Internal-detail assertions (denial reasons worded a specific
way, scope_constraint shapes) belong in `services/harness/tests/unit/
test_yaml_policy_resolver.py`, not here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from contracts.authz import AuthorizationContext, PolicyDecision, PolicyResolver
from contracts.common import Audience

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_yaml_resolver() -> PolicyResolver:
    from harness.authz.agent_profile import load_agent_profiles
    from harness.authz.killswitch import KillswitchChecker
    from harness.authz.manifest import load_tool_manifests
    from harness.authz.policy_resolver import YamlPolicyResolver

    class _NeverDisabled:
        async def is_tool_disabled(self, tool_name: str) -> bool:
            return False

        async def is_agent_disabled(self, agent_id: str) -> bool:
            return False

    tool_manifests = load_tool_manifests(REPO_ROOT / "config" / "tools", audience=Audience.INTERNAL)
    agent_profiles = load_agent_profiles(
        REPO_ROOT / "config" / "agents", audience=Audience.INTERNAL
    )
    killswitch: KillswitchChecker = _NeverDisabled()  # type: ignore[assignment]
    return YamlPolicyResolver(
        tool_manifests=tool_manifests, agent_profiles=agent_profiles, killswitch=killswitch
    )


# Every implementation under test — a future OPA/Cedar resolver adds its
# own factory here and inherits every test below for free.
RESOLVER_FACTORIES: list[Callable[[], PolicyResolver]] = [_make_yaml_resolver]


def _ctx(**overrides: object) -> AuthorizationContext:
    base = {
        "tenant_id": "tnt_demo",
        "user_id": "usr_budi",
        "employee_id": "emp_001",
        "roles": ["employee"],
        "permissions": [
            "policy.read",
            "leave.balance.read",
            "leave.request.create",
            "payslip.read",
        ],
        "scope_context": {},
        "agent_id": "hr-assistant",
        "allow_mutations": True,
    }
    base.update(overrides)
    return AuthorizationContext(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_resolver", RESOLVER_FACTORIES)
async def test_resolve_returns_a_policy_decision(
    make_resolver: Callable[[], PolicyResolver],
) -> None:
    resolver = make_resolver()
    decision = await resolver.resolve(_ctx())
    assert isinstance(decision, PolicyDecision)


@pytest.mark.asyncio
@pytest.mark.parametrize("make_resolver", RESOLVER_FACTORIES)
async def test_a_tool_with_all_required_permissions_is_allowed(
    make_resolver: Callable[[], PolicyResolver],
) -> None:
    resolver = make_resolver()
    decision = await resolver.resolve(_ctx())
    assert "get_leave_balance" in decision.allowed_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("make_resolver", RESOLVER_FACTORIES)
async def test_a_tool_missing_a_required_permission_is_denied_not_allowed(
    make_resolver: Callable[[], PolicyResolver],
) -> None:
    resolver = make_resolver()
    decision = await resolver.resolve(_ctx(permissions=["policy.read"]))  # no leave.balance.read
    assert "get_leave_balance" not in decision.allowed_tools
    assert any(d.tool_name == "get_leave_balance" for d in decision.denials)


@pytest.mark.asyncio
@pytest.mark.parametrize("make_resolver", RESOLVER_FACTORIES)
async def test_allow_mutations_false_excludes_every_mutation_kind_tool(
    make_resolver: Callable[[], PolicyResolver],
) -> None:
    resolver = make_resolver()
    decision = await resolver.resolve(
        _ctx(
            permissions=[
                "policy.read",
                "leave.balance.read",
                "leave.request.create",
                "payslip.read",
                "payroll.adjust",
            ],
            allow_mutations=False,
        )
    )
    assert "submit_leave_request" not in decision.allowed_tools
    assert "adjust_payroll" not in decision.allowed_tools
    assert "get_leave_balance" in decision.allowed_tools  # readonly unaffected


@pytest.mark.asyncio
@pytest.mark.parametrize("make_resolver", RESOLVER_FACTORIES)
async def test_a_tool_outside_the_agent_profile_is_never_allowed_regardless_of_permissions(
    make_resolver: Callable[[], PolicyResolver],
) -> None:
    resolver = make_resolver()
    # search_public_faq isn't in hr-assistant.yaml's allowed_tools at all
    # — no permission set should ever surface it for this agent_id.
    decision = await resolver.resolve(_ctx(permissions=["policy.read", "leave.balance.read"]))
    assert "search_public_faq" not in decision.allowed_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("make_resolver", RESOLVER_FACTORIES)
async def test_an_unknown_agent_id_gets_an_empty_decision_not_an_error(
    make_resolver: Callable[[], PolicyResolver],
) -> None:
    resolver = make_resolver()
    decision = await resolver.resolve(_ctx(agent_id="does-not-exist"))
    assert decision.allowed_tools == []
