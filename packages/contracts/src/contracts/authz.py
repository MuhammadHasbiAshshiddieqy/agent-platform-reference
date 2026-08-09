"""§22.1's five-set intersection, expressed as a swappable interface
(ADR-008): `PolicyResolver` is a `Protocol`, not a base class, so the M5b
POC implementation (`harness.authz.policy_resolver.YamlPolicyResolver`,
in-process, YAML-driven) and a future OPA/Cedar implementation can both
satisfy it without inheritance. `tests/conformance/test_policy_resolver.py`
tests against this interface — any implementation that passes that suite
is a legal drop-in replacement.

Lives in `contracts`, not `harness`, so the interface itself has no
dependency on which service resolves policy — mirroring why
`contracts.business_api`'s response shapes don't live inside
`mock-business-api`.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from contracts.common import DataScope, StrictModel


class ScopeConstraint(StrictModel):
    """§22.2 `data_scope` resolved against one actor's `scope_context` for
    one tool. `allowed_ids=None` means unrestricted (`tenant` scope —
    every record in the tenant is in bounds, so there's nothing to check
    against a scope_param value)."""

    data_scope: DataScope
    scope_param: str
    allowed_ids: list[str] | None = None


class Denial(StrictModel):
    """One tool excluded from `PolicyDecision.allowed_tools` despite being
    in the agent-profile/audience ceiling — §22.8's "deny wajib dicatat"."""

    tool_name: str
    reason: str
    missing_permissions: list[str] = Field(default_factory=list)


class PolicyDecision(StrictModel):
    allowed_tools: list[str] = Field(default_factory=list)
    denials: list[Denial] = Field(default_factory=list)
    scope_constraints: dict[str, ScopeConstraint] = Field(default_factory=dict)


class AuthorizationContext(StrictModel):
    """Everything `PolicyResolver.resolve` needs to compute §22.1's
    intersection for one run. Built once per run (not per tool call) —
    the resulting `PolicyDecision` is held constant for the whole run
    (§22.4: "dihitung sekali per run dan tidak berubah di tengah loop")."""

    tenant_id: str
    user_id: str
    employee_id: str | None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    scope_context: dict[str, Any] = Field(default_factory=dict)
    agent_id: str
    allow_mutations: bool = False


@runtime_checkable
class PolicyResolver(Protocol):
    async def resolve(self, ctx: AuthorizationContext) -> PolicyDecision: ...


class KillswitchRequest(StrictModel):
    """§22.6 — `POST /v1/admin/killswitch/{tools|agents}/{name}`. Gateway
    only authenticates + checks `platform.killswitch.manage` before
    proxying (boundary #2 pattern, same as the approval-decision
    endpoint) — harness owns `authz`/writes the Redis key it also reads."""

    disabled: bool
    reason: str | None = None


class KillswitchResponse(StrictModel):
    name: str
    kind: str  # "tool" | "agent"
    disabled: bool
