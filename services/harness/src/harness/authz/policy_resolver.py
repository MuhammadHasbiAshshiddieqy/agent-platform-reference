"""§22.1's five-set intersection (ADR-008: in-process, YAML-driven, behind
the `contracts.authz.PolicyResolver` Protocol so a future OPA/Cedar
implementation is a drop-in). `YamlPolicyResolver.resolve` is called
exactly once per run (`graph/build.py`'s `authorize` node) — the
resulting `PolicyDecision` is held on `AgentState` for the rest of the
run, never recomputed mid `respond`<->`act` loop (§22.4).

```
effective_tools =
      agent_profile.allowed_tools          # plafon desain
    ∩ deployment.audience_tools            # plafon deployment (§21.2)
    ∩ {t | user.permissions ⊇ t.required_permissions}
    ∩ {t | request.allow_mutations ∨ t.kind == readonly}
    ∩ {t | ¬killswitch(t)}
```

Only tools in `agent_profile.allowed_tools ∩ deployment.audience_tools`
(the "designed and deployed" ceiling) are candidates at all — a tool
outside the agent's profile was never going to be offered regardless of
permission, so it doesn't generate a `Denial` (that set difference is
routine, not a signal). Every candidate that fails permission/mutation-
scope/killswitch DOES generate a `Denial` — §22.8's "spike in `deny` is
itself a signal" only works if `deny` is recorded for genuine attempts,
not for tools a user was never shown to begin with.
"""

from __future__ import annotations

from contracts.authz import AuthorizationContext, Denial, PolicyDecision, ScopeConstraint
from contracts.common import DataScope, ToolKind
from harness.authz.agent_profile import AgentProfile
from harness.authz.killswitch import KillswitchChecker
from harness.authz.manifest import ToolManifestEntry


def _resolve_scope_constraint(
    tool: ToolManifestEntry, ctx: AuthorizationContext
) -> ScopeConstraint | None:
    if tool.scope_param is None:
        return None
    if tool.data_scope == DataScope.SELF:
        allowed = [ctx.employee_id] if ctx.employee_id else []
    elif tool.data_scope == DataScope.TEAM:
        # §22.2: row-level, and "wajib divalidasi ulang di business-api" —
        # harness passes through what the JWT's scope_context asserts,
        # business-api is the actual source of truth for org structure.
        team_member_ids = ctx.scope_context.get("team_member_ids", [])
        allowed = list(team_member_ids) if isinstance(team_member_ids, list) else []
        if ctx.employee_id:
            allowed = [*allowed, ctx.employee_id]
    elif tool.data_scope == DataScope.DEPARTMENT:
        # No tool in this milestone's manifest set uses department scope
        # (harness doesn't hold a department->employee directory to
        # expand this into concrete ids) — recorded as unrestricted at
        # this layer, deferring to business-api, same as `tenant`.
        return ScopeConstraint(
            data_scope=tool.data_scope, scope_param=tool.scope_param, allowed_ids=None
        )
    else:  # tenant
        return ScopeConstraint(
            data_scope=tool.data_scope, scope_param=tool.scope_param, allowed_ids=None
        )
    return ScopeConstraint(
        data_scope=tool.data_scope, scope_param=tool.scope_param, allowed_ids=allowed
    )


class YamlPolicyResolver:
    def __init__(
        self,
        *,
        tool_manifests: dict[str, ToolManifestEntry],
        agent_profiles: dict[str, AgentProfile],
        killswitch: KillswitchChecker,
    ) -> None:
        self._tool_manifests = tool_manifests
        self._agent_profiles = agent_profiles
        self._killswitch = killswitch

    async def resolve(self, ctx: AuthorizationContext) -> PolicyDecision:
        profile = self._agent_profiles.get(ctx.agent_id)
        if profile is None:
            # Unknown/wrong-audience agent_id — nothing is in scope. Not a
            # Denial per-tool (there's no tool ceiling to compare against),
            # just an empty decision.
            return PolicyDecision()

        candidates = [
            self._tool_manifests[name]
            for name in profile.allowed_tools
            if name in self._tool_manifests  # deployment audience may exclude it entirely
        ]

        allowed: list[str] = []
        denials: list[Denial] = []
        scope_constraints: dict[str, ScopeConstraint] = {}

        for tool in candidates:
            missing = [p for p in tool.required_permissions if p not in ctx.permissions]
            if missing:
                denials.append(
                    Denial(
                        tool_name=tool.name,
                        reason="missing required permissions",
                        missing_permissions=missing,
                    )
                )
                continue

            if tool.kind == ToolKind.MUTATION and not ctx.allow_mutations:
                denials.append(
                    Denial(tool_name=tool.name, reason="mutations are not allowed for this request")
                )
                continue

            if await self._killswitch.is_tool_disabled(tool.name):
                denials.append(Denial(tool_name=tool.name, reason="tool disabled by killswitch"))
                continue
            if await self._killswitch.is_agent_disabled(ctx.agent_id):
                denials.append(Denial(tool_name=tool.name, reason="agent disabled by killswitch"))
                continue

            constraint = _resolve_scope_constraint(tool, ctx)
            if constraint is not None and constraint.allowed_ids == []:
                # data_scope: self with no employee_id to scope to — there
                # is no valid record this actor could ever act on.
                denials.append(
                    Denial(tool_name=tool.name, reason="no scope identity available for this tool")
                )
                continue

            allowed.append(tool.name)
            if constraint is not None:
                scope_constraints[tool.name] = constraint

        return PolicyDecision(
            allowed_tools=allowed, denials=denials, scope_constraints=scope_constraints
        )
