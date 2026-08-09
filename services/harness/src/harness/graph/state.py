"""AgentState. M3 adds `retrieve` (§5.3's target structure); M4 adds
input/output guardrails (§9); M5 adds `act` (tool calling + mutations,
§8.4); M5b adds `authorize` (§22's PolicyResolver, computed once per run);
M7 adds `cache_lookup`/`cache_write` (§10's semantic cache).
"""

from __future__ import annotations

from typing import Any

from contracts.agent import Citation, PendingApproval
from contracts.authz import Denial, ScopeConstraint
from harness.authz.records import AuthzDecisionRecord
from harness.guardrails.events import GuardrailEvent
from harness.tools.records import MutationRequestRecord, ToolInvocationRecord
from pydantic import BaseModel, Field


class RetrievedChunkState(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    source_uri: str
    score: float


class ToolCallState(BaseModel):
    """One model-requested call, OpenAI tool-calling shape (`id` is what
    ties the eventual `role: tool` result message back to this call)."""

    id: str
    name: str
    arguments: dict[str, Any]


class AgentState(BaseModel):
    trace_id: str
    tenant_id: str
    user_id: str
    agent_id: str
    acl_group_ids: list[str] = Field(default_factory=list)
    # §22.3's full claim shape.
    employee_id: str | None = None
    permissions: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    scope_context: dict[str, Any] = Field(default_factory=dict)
    subject_token: str | None = None
    allow_mutations: bool = False
    input_text: str
    model_alias: str = "agent-primary"
    # §6 L3 — async-worker's async-pool virtual key, applied to the
    # primary generation call only (see graph/build.py's `respond` node);
    # `None` for every sync request.
    model_router_key_override: str | None = None

    # §22.1/§22.4 — computed once by the `authorize` node, held constant
    # for the rest of the run (never recomputed mid respond<->act loop).
    allowed_tools: list[str] = Field(default_factory=list)
    scope_constraints: dict[str, ScopeConstraint] = Field(default_factory=dict)
    authz_denials: list[Denial] = Field(default_factory=list)
    authz_decision_records: list[AuthzDecisionRecord] = Field(default_factory=list)

    retrieved_chunks: list[RetrievedChunkState] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)

    # §10 semantic cache. `cache_query_embedding` is stashed by
    # `cache_lookup` (hit or miss) so `cache_write` never re-embeds the
    # same query a second time.
    cache_hit: bool = False
    cache_query_embedding: list[float] | None = None

    # §9 guardrails. `pii_mapping` never leaves this process — it's run-
    # scoped working memory for restoring the user's own redacted input
    # in the final output (guardrails/pii.py), not persisted anywhere.
    pii_mapping: dict[str, str] = Field(default_factory=dict)
    refused: bool = False
    refusal_reason: str | None = None
    guardrail_events: list[GuardrailEvent] = Field(default_factory=list)

    # §5.3/§8.4 tool calling + mutations. `messages` is the actual running
    # OpenAI-format conversation for this turn (system/user/assistant/tool
    # roles) — kept in state rather than rebuilt each node so assistant
    # `tool_calls` messages and their matching `tool` result messages stay
    # correctly interleaved across `respond` <-> `act` round-trips.
    messages: list[dict[str, Any]] = Field(default_factory=list)
    pending_tool_calls: list[ToolCallState] = Field(default_factory=list)
    tool_iterations: int = 0
    # §13.1's `_eval.tools_offered` — the UNION of every tool schema ever
    # offered to the model across every `respond` call this turn, not
    # just the last one. `capability_leak` (§13.3) checks membership in
    # this set, so a tool that appeared once and was never called still
    # counts as a leak — offering it at all is the violation.
    tools_offered: list[str] = Field(default_factory=list)
    mutations_previewed: list[str] = Field(default_factory=list)
    mutations_executed: list[str] = Field(default_factory=list)
    pending_approvals: list[PendingApproval] = Field(default_factory=list)
    tool_invocation_records: list[ToolInvocationRecord] = Field(default_factory=list)
    mutation_request_records: list[MutationRequestRecord] = Field(default_factory=list)

    output_text: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
