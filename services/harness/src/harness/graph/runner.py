"""Ties the graph, persistence, and tracing together for one run. This is
what `POST /internal/v1/runs` calls — kept out of the FastAPI route itself
so it stays testable without spinning up HTTP.
"""

from __future__ import annotations

import time
import uuid

from contracts.agent import AgentOutput, Usage
from contracts.common import Audience
from contracts.eval import EvalDebugBundle
from contracts.gateway import AgentInvokeResponse
from contracts.harness import AgentRunRequest
from harness.config import settings
from harness.eval_bundle import build_eval_bundle, eval_mode_abuse_event
from harness.graph.build import PROMPT_VERSION, HarnessGraph
from harness.graph.state import AgentState
from harness.observability.tracing import record_run
from harness.persistence.db import tenant_session
from harness.persistence.repository import (
    ensure_conversation,
    insert_agent_run,
    insert_authz_decision,
    insert_guardrail_event,
    insert_message,
    insert_mutation_request,
    insert_tool_invocation,
)
from langfuse import Langfuse

# placeholder until agent profiles carry their own version-bump policy
AGENT_VERSION = "harness@0.1.0"


async def run_agent(
    graph: HarnessGraph, langfuse: Langfuse, request: AgentRunRequest, audience: Audience
) -> AgentInvokeResponse:
    conversation_id = request.conversation_id or f"conv_{uuid.uuid4().hex[:20]}"
    started = time.perf_counter()

    initial_state = AgentState(
        trace_id=request.trace_id,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
        acl_group_ids=request.acl_group_ids,
        employee_id=request.employee_id,
        permissions=request.permissions,
        roles=request.roles,
        scope_context=request.scope_context,
        subject_token=request.subject_token,
        allow_mutations=request.options.allow_mutations,
        input_text=request.input.content,
        model_router_key_override=request.model_router_key_override,
    )
    result_state = await graph.ainvoke(
        initial_state, config={"configurable": {"thread_id": conversation_id}}
    )
    output_text = result_state["output_text"]
    input_tokens = result_state["input_tokens"]
    output_tokens = result_state["output_tokens"]
    cost_usd = result_state["cost_usd"]
    citations = result_state["citations"]
    degraded = result_state["degraded"]
    refused = result_state["refused"]
    guardrail_events = result_state["guardrail_events"]
    pending_approvals = result_state["pending_approvals"]
    tool_invocation_records = result_state["tool_invocation_records"]
    mutation_request_records = result_state["mutation_request_records"]
    authz_decision_records = result_state["authz_decision_records"]
    cache_hit = result_state["cache_hit"]
    latency_ms = int((time.perf_counter() - started) * 1000)

    # §13.1 — harness alone decides whether to honor `X-Eval-Mode`,
    # never trusting gateway's own tenant check as sufficient (§8.4).
    eval_bundle: EvalDebugBundle | None = None
    if request.eval_mode_requested:
        if request.tenant_id in settings.eval_tenant_id_set:
            eval_bundle = build_eval_bundle(result_state, prompt_version=PROMPT_VERSION)
        else:
            guardrail_events = [*guardrail_events, eval_mode_abuse_event()]

    async with tenant_session(request.tenant_id) as conn:
        await ensure_conversation(
            conn,
            conversation_id=conversation_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            agent_id=request.agent_id,
        )
        await insert_message(
            conn,
            message_id=f"msg_{uuid.uuid4().hex[:20]}",
            conversation_id=conversation_id,
            tenant_id=request.tenant_id,
            role="user",
            content={"type": "text", "content": request.input.content},
        )
        await insert_message(
            conn,
            message_id=f"msg_{uuid.uuid4().hex[:20]}",
            conversation_id=conversation_id,
            tenant_id=request.tenant_id,
            role="assistant",
            content={"type": "text", "content": output_text},
        )
        await insert_agent_run(
            conn,
            run_id=request.run_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            conversation_id=conversation_id,
            agent_id=request.agent_id,
            agent_version=AGENT_VERSION,
            prompt_version=PROMPT_VERSION,
            execution_mode=request.execution_mode.value,
            status="blocked" if refused else "succeeded",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
        )
        for event in guardrail_events:
            await insert_guardrail_event(
                conn,
                event_id=f"gev_{uuid.uuid4().hex[:20]}",
                run_id=request.run_id,
                trace_id=request.trace_id,
                tenant_id=request.tenant_id,
                stage=event.stage,
                rule_id=event.rule_id,
                severity=event.severity,
                action_taken=event.action_taken,
                detail=event.detail,
            )
            event.record_metric()
        for invocation in tool_invocation_records:
            await insert_tool_invocation(
                conn,
                invocation_id=f"tiv_{uuid.uuid4().hex[:20]}",
                run_id=request.run_id,
                trace_id=request.trace_id,
                tenant_id=request.tenant_id,
                tool_name=invocation.tool_name,
                tool_kind=invocation.tool_kind,
                arguments=invocation.arguments,
                result_summary=invocation.result_summary,
                status=invocation.status,
            )
        for mutation in mutation_request_records:
            await insert_mutation_request(
                conn,
                mutation_id=mutation.id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                tenant_id=request.tenant_id,
                actor_user_id=request.user_id,
                action_name=mutation.action_name,
                risk_level=mutation.risk_level,
                preview_payload=mutation.preview_payload,
                approval_id=mutation.approval_id,
                idempotency_key=mutation.idempotency_key,
                status=mutation.status,
                business_ref=mutation.business_ref,
                executed_at=mutation.executed_at,
            )
        for authz_record in authz_decision_records:
            await insert_authz_decision(
                conn,
                decision_id=f"azd_{uuid.uuid4().hex[:20]}",
                run_id=request.run_id,
                trace_id=request.trace_id,
                tenant_id=request.tenant_id,
                actor_user_id=request.user_id,
                actor_roles=request.roles,
                agent_id=request.agent_id,
                audience=audience.value,
                tool_name=authz_record.tool_name,
                decision=authz_record.decision,
                deny_reason=authz_record.deny_reason,
                missing_permissions=authz_record.missing_permissions,
                data_scope=authz_record.data_scope,
                scope_satisfied=authz_record.scope_satisfied,
            )

    chunk_content_by_id = {c.chunk_id: c.content for c in result_state["retrieved_chunks"]}
    cited_chunks = [
        {
            "chunk_id": citation.chunk_id,
            "source_uri": citation.source_uri,
            "content": chunk_content_by_id.get(citation.chunk_id, ""),
        }
        for citation in citations
    ]

    record_run(
        langfuse,
        trace_id=request.trace_id,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
        execution_mode=request.execution_mode.value,
        model_alias=initial_state.model_alias,
        input_text=request.input.content,
        output_text=output_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_hit=cache_hit,
        cited_chunks=cited_chunks,
    )

    return AgentInvokeResponse(
        run_id=request.run_id,
        conversation_id=conversation_id,
        trace_id=request.trace_id,
        output=AgentOutput(content=output_text, citations=citations),
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd),
        degraded=degraded,
        pending_approvals=pending_approvals,
        cache_hit=cache_hit,
        # mypy's pydantic plugin only recognizes the "_eval" alias as a
        # constructor kwarg here; `populate_by_name=True`
        # (contracts.common.StrictModel) makes `eval=` the real field
        # name at runtime too — confirmed live (`_eval` shows up
        # correctly in the actual JSON response).
        eval=eval_bundle,  # type: ignore[call-arg]
    )
