"""§13.1's `_eval` debug bundle assembly, plus the `EVAL_TENANT_IDS` gate
that decides whether to honor a request's `X-Eval-Mode` header at all.
Harness is the sole authority here — `contracts.harness.AgentRunRequest.
eval_mode_requested`'s docstring explains why gateway's own tenant
resolution isn't trusted as sufficient on its own (§8.4).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from contracts.eval import EvalDebugBundle, GuardrailEventRecord, ToolCallRecord
from harness.guardrails.events import GuardrailEvent


def args_hash(arguments: dict[str, object]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_eval_bundle(result_state: dict[str, Any], *, prompt_version: str) -> EvalDebugBundle:
    """Reads the raw LangGraph result dict directly — same access pattern
    `graph/runner.py`'s own extraction block already uses for every other
    field, rather than paying to re-validate a full `AgentState` just for
    this.
    """
    # §11.2/§13.1: retrieval-service already fits every retrieved chunk
    # into the prompt at assembly time (M3's own design, reaffirmed in
    # `graph/build.py`'s `respond` node) — "retrieved" and "in prompt"
    # are the same set here, so both fields share one source list.
    chunk_ids = [c.chunk_id for c in result_state["retrieved_chunks"]]
    return EvalDebugBundle(
        tools_offered=result_state["tools_offered"],
        tools_called=[
            ToolCallRecord(
                name=record.tool_name, args_hash=args_hash(record.arguments), status=record.status
            )
            for record in result_state["tool_invocation_records"]
        ],
        mutations_executed=result_state["mutations_executed"],
        mutations_previewed=result_state["mutations_previewed"],
        retrieved_chunk_ids=chunk_ids,
        chunks_in_prompt=chunk_ids,
        retrieved_chunk_contents={c.chunk_id: c.content for c in result_state["retrieved_chunks"]},
        refused=result_state["refused"],
        refusal_reason=result_state["refusal_reason"],
        guardrail_events=[
            GuardrailEventRecord(
                stage=event.stage, rule_id=event.rule_id, action=event.action_taken
            )
            for event in result_state["guardrail_events"]
        ],
        prompt_version=prompt_version,
        model_alias=result_state["model_alias"],
        iterations=result_state["tool_iterations"],
    )


def eval_mode_abuse_event() -> GuardrailEvent:
    """§13.1: `X-Eval-Mode` requested for a tenant outside `EVAL_TENANT_
    IDS`. The underlying request still proceeds normally — only the
    debug-bundle grant is denied — so `block` here describes the denied
    *capability*, not the whole request, same convention `authz/
    manifest.py`'s tool-deny path uses for a partial denial within an
    otherwise-successful run."""
    return GuardrailEvent(
        stage="input",
        rule_id="eval_mode_abuse",
        severity="warning",
        action_taken="block",
        detail=None,
    )
