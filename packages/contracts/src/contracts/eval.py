"""§13.1 debug bundle + §13.2 eval item — what makes eval deterministic.

`EvalDebugBundle` is attached to a gateway response as `_eval` only when
`X-Eval-Mode` is honored (tenant_id ∈ EVAL_TENANT_IDS, §13.1). Everything
here is read by `eval-service` metrics (§13.3) — changing a field name here
without updating the metric that reads it silently breaks a CI gate.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from contracts.common import GuardrailAction, GuardrailStage, StrictModel, ToolInvocationStatus


class ToolCallRecord(StrictModel):
    name: str
    args_hash: str
    status: ToolInvocationStatus


class GuardrailEventRecord(StrictModel):
    stage: GuardrailStage
    rule_id: str
    action: GuardrailAction


class EvalDebugBundle(StrictModel):
    """§13.1 — the `_eval` field on an invoke response."""

    tools_offered: list[str] = Field(default_factory=list)
    tools_called: list[ToolCallRecord] = Field(default_factory=list)
    mutations_executed: list[str] = Field(default_factory=list)
    mutations_previewed: list[str] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    chunks_in_prompt: list[str] = Field(default_factory=list)
    # Beyond §13.1's literal example: the Ragas judged metrics (§13.4 —
    # faithfulness/context_precision/context_recall) need the actual
    # chunk TEXT, not just ids, to judge against. eval-service can't
    # fetch this itself afterward — it isn't on retrieval-service's
    # network and `catalog.chunks` is retrieval's schema, not eval's
    # (boundary #2) — so harness includes it here, keyed by chunk_id.
    retrieved_chunk_contents: dict[str, str] = Field(default_factory=dict)
    refused: bool
    refusal_reason: str | None = None
    guardrail_events: list[GuardrailEventRecord] = Field(default_factory=list)
    prompt_version: str
    model_alias: str
    iterations: int = Field(ge=0)


class EvalItem(StrictModel):
    """§13.2 — mirrors `eval.items` (extended version, supersedes §8.5's)."""

    id: str
    dataset_id: str
    question: str
    ground_truth: str | None = None
    contexts: list[str] | None = None

    # actor context: item is run AS this user
    actor_user_id: str
    actor_role: str
    actor_acl_group_ids: list[str] = Field(default_factory=list)

    # expected agent behavior
    expected_required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    allowed_mutations: list[str] = Field(default_factory=list)
    should_refuse: bool = False
    forbidden_output_terms: list[str] = Field(default_factory=list)
    allowed_pii: list[str] = Field(default_factory=list)
    expects_citation: bool = True

    tags: list[str] = Field(default_factory=list)  # rag | rbac | mutation | refusal | cache
    difficulty: str | None = None
    source_trace: str | None = None
    created_at: datetime | None = None
