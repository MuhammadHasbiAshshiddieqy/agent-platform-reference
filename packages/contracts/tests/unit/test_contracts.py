"""Sanity tests for the shared contracts — validated against the exact JSON
shapes shown in §8 of the spec, not invented fixtures."""

from __future__ import annotations

import pytest
from contracts.agent import AgentInput, RunOptions
from contracts.business_api import ExecuteRequest, MutationEffect, PreviewResponse
from contracts.common import Headers, RiskLevel
from contracts.eval import EvalDebugBundle
from contracts.gateway import AgentInvokeRequest
from contracts.harness import AgentRunRequest
from pydantic import ValidationError


def test_invoke_request_matches_spec_example() -> None:
    # §8.1 request example
    req = AgentInvokeRequest.model_validate(
        {
            "conversation_id": "conv_01HX...",
            "agent_id": "hr-assistant",
            "input": {"type": "text", "content": "Berapa sisa cuti saya tahun ini?"},
            "context": {"locale": "id-ID", "channel": "web"},
            "options": {"stream": False, "max_output_tokens": 1024, "allow_mutations": False},
        }
    )
    assert req.input.content.startswith("Berapa")
    assert req.options.allow_mutations is False


def test_invoke_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AgentInvokeRequest.model_validate(
            {
                "agent_id": "hr-assistant",
                "input": {"type": "text", "content": "hi"},
                "typo_field": True,
            }
        )


def test_preview_response_effects_use_from_to_alias() -> None:
    # §8.4 preview response example — "from"/"to" are reserved-ish in Python,
    # the model must still round-trip the wire shape verbatim.
    resp = PreviewResponse.model_validate(
        {
            "action": "submit_leave_request",
            "risk_level": "medium",
            "requires_approval": True,
            "effects": [
                {"resource": "leave_balance", "id": "emp_001", "from": 8, "to": 5},
                {"resource": "leave_request", "id": None, "operation": "create"},
            ],
            "reversible": True,
            "preview_token": "prv_01HX...",
            "validation_errors": [],
        }
    )
    assert resp.risk_level == RiskLevel.MEDIUM
    balance_effect: MutationEffect = resp.effects[0]
    assert balance_effect.from_ == 8
    assert balance_effect.to == 5
    # alias round-trips back out as "from", not "from_"
    dumped = balance_effect.model_dump(by_alias=True)
    assert dumped["from"] == 8
    assert "from_" not in dumped


def test_execute_request_only_accepts_preview_token() -> None:
    execute = ExecuteRequest.model_validate(
        {"preview_token": "prv_01HX...", "approval_id": "apr_01HX..."}
    )
    assert execute.approval_id == "apr_01HX..."
    with pytest.raises(ValidationError):
        ExecuteRequest.model_validate(
            {"preview_token": "prv_01HX...", "params": {"leave_days": 999}}
        )


def test_agent_run_request_carries_tenant_and_trace_through() -> None:
    run = AgentRunRequest.model_validate(
        {
            "run_id": "run_01HX",
            "trace_id": "trc_01HX",
            "tenant_id": "tnt_demo",
            "user_id": "usr_budi",
            "acl_group_ids": ["grp_all_staff", "grp_engineering"],
            "agent_id": "hr-assistant",
            "conversation_id": None,
            "input": AgentInput(content="Berapa sisa cuti saya?"),
            "options": RunOptions(),
            "execution_mode": "sync",
            "budget": {"pool": "sync", "reserved_tokens": 2451},
        }
    )
    assert run.tenant_id == "tnt_demo"
    assert run.budget.reserved_tokens == 2451


def test_eval_debug_bundle_distinguishes_retrieved_from_in_prompt() -> None:
    # §13.1 — citation_validity depends on this distinction staying intact.
    bundle = EvalDebugBundle.model_validate(
        {
            "tools_offered": ["get_leave_balance"],
            "tools_called": [{"name": "get_leave_balance", "args_hash": "a1b2", "status": "ok"}],
            "mutations_executed": [],
            "mutations_previewed": [],
            "retrieved_chunk_ids": ["chk_9", "chk_14", "chk_22"],
            "chunks_in_prompt": ["chk_9", "chk_14"],
            "refused": False,
            "refusal_reason": None,
            "guardrail_events": [],
            "prompt_version": "hr_assistant@v7",
            "model_alias": "agent-primary",
            "iterations": 2,
        }
    )
    assert "chk_22" in bundle.retrieved_chunk_ids
    assert "chk_22" not in bundle.chunks_in_prompt


def test_header_constants_match_spec_casing() -> None:
    assert Headers.IDEMPOTENCY_KEY == "Idempotency-Key"
    assert Headers.TRACE_ID == "X-Trace-Id"
    assert Headers.EVAL_MODE == "X-Eval-Mode"
