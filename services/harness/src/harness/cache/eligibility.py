"""§10's cache-write eligibility gate — every condition must hold, not
just one. `tool_manifests`/`agent_profiles` are the same dicts loaded
once at boot (§21/§22.7) — no new config surface for this milestone.
"""

from __future__ import annotations

from harness.authz.agent_profile import AgentProfile
from harness.authz.manifest import ToolManifestEntry
from harness.graph.state import AgentState

# §9.2's `action_taken == "flag"` is groundedness's advisory-only signal
# — a run that surfaced one produced a real answer the guardrail
# pipeline let through, but confidence was too low to reuse for a
# different user's question later.
_PII_RULE_IDS = {"pii_redaction", "pii_leakage"}


def is_cacheable(
    state: AgentState,
    *,
    agent_profiles: dict[str, AgentProfile],
    tool_manifests: dict[str, ToolManifestEntry],
) -> bool:
    profile = agent_profiles.get(state.agent_id)
    if profile is None or not profile.cacheable:
        return False
    if state.refused:
        return False
    if "retrieval" in state.degraded:
        # A degraded RAG pass (retrieval-service down mid-run) produced
        # this answer from incomplete context — caching it risks serving
        # a stale/incomplete answer for the full TTL even after
        # retrieval recovers. Not one of §10's literal 5 conditions, but
        # the same "don't cache a low-confidence answer" reasoning as
        # the groundedness-flag check below.
        return False
    if any(event.action_taken == "flag" for event in state.guardrail_events):
        return False
    if any(event.rule_id in _PII_RULE_IDS for event in state.guardrail_events):
        # Covers both directions: `pii_redaction` (input) means the
        # final text has this user's own PII restored into it
        # (guardrails/pii.py's `restore_input_pii`) — not safe to hand
        # to a different user even sharing the same acl_hash.
        # `pii_leakage` (output) means the model generated PII on its
        # own; redacted or not, treat the whole run as too sensitive to
        # reuse rather than caching a redacted-but-still-personal answer.
        return False
    for record in state.tool_invocation_records:
        manifest = tool_manifests.get(record.tool_name)
        if manifest is None or not manifest.cacheable:
            return False
    return True
