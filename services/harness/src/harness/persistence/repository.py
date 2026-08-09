"""§8.5 conversation schema writes. Raw SQL via SQLAlchemy Core `text()` —
consistent with migrations/ (no ORM), and this module is the only place
that's allowed to know the table shapes.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def ensure_conversation(
    conn: AsyncConnection,
    *,
    conversation_id: str,
    tenant_id: str,
    user_id: str,
    agent_id: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO conversation.conversations (id, tenant_id, user_id, agent_id) "
            "VALUES (:id, :tenant_id, :user_id, :agent_id) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": conversation_id, "tenant_id": tenant_id, "user_id": user_id, "agent_id": agent_id},
    )


async def insert_message(
    conn: AsyncConnection,
    *,
    message_id: str,
    conversation_id: str,
    tenant_id: str,
    role: str,
    content: dict[str, Any],
) -> None:
    await conn.execute(
        text(
            "INSERT INTO conversation.messages (id, conversation_id, tenant_id, role, content) "
            "VALUES (:id, :conversation_id, :tenant_id, :role, CAST(:content AS jsonb))"
        ),
        {
            "id": message_id,
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "role": role,
            "content": json.dumps(content),
        },
    )


async def insert_guardrail_event(
    conn: AsyncConnection,
    *,
    event_id: str,
    run_id: str,
    trace_id: str,
    tenant_id: str,
    stage: str,
    rule_id: str,
    severity: str,
    action_taken: str,
    detail: dict[str, Any] | None,
) -> None:
    """§8.5 `audit.guardrail_events` (migration 0003) — one row per
    `GuardrailEvent` collected in `AgentState.guardrail_events` (§9)."""
    await conn.execute(
        text(
            "INSERT INTO audit.guardrail_events "
            "(id, run_id, trace_id, tenant_id, stage, rule_id, severity, action_taken, detail) "
            "VALUES (:id, :run_id, :trace_id, :tenant_id, :stage, :rule_id, :severity, "
            " :action_taken, CAST(:detail AS jsonb))"
        ),
        {
            "id": event_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "stage": stage,
            "rule_id": rule_id,
            "severity": severity,
            "action_taken": action_taken,
            "detail": json.dumps(detail) if detail is not None else None,
        },
    )


async def insert_tool_invocation(
    conn: AsyncConnection,
    *,
    invocation_id: str,
    run_id: str,
    trace_id: str,
    tenant_id: str,
    tool_name: str,
    tool_kind: str,
    arguments: dict[str, Any],
    result_summary: dict[str, Any] | None,
    status: str,
) -> None:
    """§8.5 `audit.tool_invocations` (migration 0003) — one row per model-
    requested tool call (§5.3 `act`), readonly or mutation."""
    await conn.execute(
        text(
            "INSERT INTO audit.tool_invocations "
            "(id, run_id, trace_id, tenant_id, tool_name, tool_kind, arguments, "
            " result_summary, status) "
            "VALUES (:id, :run_id, :trace_id, :tenant_id, :tool_name, :tool_kind, "
            " CAST(:arguments AS jsonb), CAST(:result_summary AS jsonb), :status)"
        ),
        {
            "id": invocation_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "tool_name": tool_name,
            "tool_kind": tool_kind,
            "arguments": json.dumps(arguments),
            "result_summary": json.dumps(result_summary) if result_summary is not None else None,
            "status": status,
        },
    )


async def insert_mutation_request(
    conn: AsyncConnection,
    *,
    mutation_id: str,
    run_id: str,
    trace_id: str,
    tenant_id: str,
    actor_user_id: str,
    action_name: str,
    risk_level: str,
    preview_payload: dict[str, Any],
    approval_id: str | None,
    idempotency_key: str,
    status: str,
    business_ref: str | None,
    executed_at: datetime | None,
) -> None:
    """§8.5 `audit.mutation_requests` (migration 0003) — the initial
    insert made from the agent run. The approval decision endpoint
    updates this same row later (`try_claim_mutation_decision` /
    `update_mutation_request_execution_result` below), a separate request
    entirely."""
    await conn.execute(
        text(
            "INSERT INTO audit.mutation_requests "
            "(id, run_id, trace_id, tenant_id, actor_user_id, action_name, risk_level, "
            " preview_payload, approval_id, idempotency_key, status, business_ref, executed_at) "
            "VALUES (:id, :run_id, :trace_id, :tenant_id, :actor_user_id, :action_name, "
            " :risk_level, CAST(:preview_payload AS jsonb), :approval_id, :idempotency_key, "
            " :status, :business_ref, CAST(:executed_at AS timestamptz))"
        ),
        {
            "id": mutation_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "action_name": action_name,
            "risk_level": risk_level,
            "preview_payload": json.dumps(preview_payload),
            "approval_id": approval_id,
            "idempotency_key": idempotency_key,
            "status": status,
            "business_ref": business_ref,
            "executed_at": executed_at,
        },
    )


async def upsert_tool_override(
    conn: AsyncConnection, *, tool_name: str, disabled: bool, reason: str | None, changed_by: str
) -> None:
    """§22.6/§22.8 `authz.tool_overrides` — durable record of a killswitch
    flip, alongside the Redis key `KillswitchChecker` actually reads at
    request time (Redis is the fast path with a 10s cache; this table is
    what survives a Redis restart and gives `changed_by`/`changed_at` an
    audit trail Redis alone wouldn't)."""
    await conn.execute(
        text(
            "INSERT INTO authz.tool_overrides "
            "(tool_name, disabled, reason, changed_by, changed_at) "
            "VALUES (:tool_name, :disabled, :reason, :changed_by, now()) "
            "ON CONFLICT (tool_name) DO UPDATE SET "
            "disabled = EXCLUDED.disabled, reason = EXCLUDED.reason, "
            "changed_by = EXCLUDED.changed_by, changed_at = now()"
        ),
        {"tool_name": tool_name, "disabled": disabled, "reason": reason, "changed_by": changed_by},
    )


async def insert_authz_decision(
    conn: AsyncConnection,
    *,
    decision_id: str,
    run_id: str,
    trace_id: str,
    tenant_id: str,
    actor_user_id: str,
    actor_roles: list[str],
    agent_id: str,
    audience: str,
    tool_name: str,
    decision: str,
    deny_reason: str | None,
    missing_permissions: list[str],
    data_scope: str | None,
    scope_satisfied: bool | None,
) -> None:
    """§22.8 `audit.authz_decisions` (migration 0007) — one row per tool
    candidate the `authorize` node evaluated, `allow` and `deny` alike
    (§22.8: "deny wajib dicatat, bukan hanya allow")."""
    await conn.execute(
        text(
            "INSERT INTO audit.authz_decisions "
            "(id, run_id, trace_id, tenant_id, actor_user_id, actor_roles, agent_id, "
            " audience, tool_name, decision, deny_reason, missing_permissions, "
            " data_scope, scope_satisfied) "
            "VALUES (:id, :run_id, :trace_id, :tenant_id, :actor_user_id, :actor_roles, "
            " :agent_id, :audience, :tool_name, :decision, :deny_reason, "
            " :missing_permissions, :data_scope, :scope_satisfied)"
        ),
        {
            "id": decision_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
            "actor_roles": actor_roles,
            "agent_id": agent_id,
            "audience": audience,
            "tool_name": tool_name,
            "decision": decision,
            "deny_reason": deny_reason,
            "missing_permissions": missing_permissions,
            "data_scope": data_scope,
            "scope_satisfied": scope_satisfied,
        },
    )


async def get_mutation_request_by_approval_id(
    conn: AsyncConnection, *, tenant_id: str, approval_id: str
) -> dict[str, Any] | None:
    result = await conn.execute(
        text(
            "SELECT id, run_id, trace_id, actor_user_id, action_name, risk_level, "
            "preview_payload, approval_id, idempotency_key, status, business_ref "
            "FROM audit.mutation_requests "
            "WHERE tenant_id = :tenant_id AND approval_id = :approval_id"
        ),
        {"tenant_id": tenant_id, "approval_id": approval_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def try_claim_mutation_decision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    approval_id: str,
    new_status: str,
    approved_by: str | None,
) -> bool:
    """§23.2i's race-safe transition: `UPDATE ... WHERE status =
    'awaiting_approval'`, then check rowcount. Returns True if this call
    won the race, False if the row was already decided (by anyone,
    including a concurrent identical request) or never existed."""
    if new_status == "approved":
        result = await conn.execute(
            text(
                "UPDATE audit.mutation_requests "
                "SET status = :new_status, approved_by = :approved_by, approved_at = now() "
                "WHERE tenant_id = :tenant_id AND approval_id = :approval_id "
                "AND status = 'awaiting_approval'"
            ),
            {
                "new_status": new_status,
                "approved_by": approved_by,
                "tenant_id": tenant_id,
                "approval_id": approval_id,
            },
        )
    else:
        result = await conn.execute(
            text(
                "UPDATE audit.mutation_requests SET status = :new_status "
                "WHERE tenant_id = :tenant_id AND approval_id = :approval_id "
                "AND status = 'awaiting_approval'"
            ),
            {"new_status": new_status, "tenant_id": tenant_id, "approval_id": approval_id},
        )
    return result.rowcount == 1


async def update_mutation_request_execution_result(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    approval_id: str,
    status: str,
    business_ref: str | None,
) -> None:
    await conn.execute(
        text(
            "UPDATE audit.mutation_requests SET status = :status, business_ref = :business_ref, "
            "executed_at = CASE WHEN :status = 'executed' THEN now() ELSE executed_at END "
            "WHERE tenant_id = :tenant_id AND approval_id = :approval_id"
        ),
        {
            "status": status,
            "business_ref": business_ref,
            "tenant_id": tenant_id,
            "approval_id": approval_id,
        },
    )


async def insert_agent_run(
    conn: AsyncConnection,
    *,
    run_id: str,
    trace_id: str,
    tenant_id: str,
    conversation_id: str,
    agent_id: str,
    agent_version: str,
    prompt_version: str,
    execution_mode: str,
    status: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_ms: int,
    cache_hit: bool = False,
    error_code: str | None = None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO conversation.agent_runs "
            "(id, trace_id, tenant_id, conversation_id, agent_id, agent_version, "
            " prompt_version, execution_mode, status, input_tokens, output_tokens, "
            " cost_usd, cache_hit, latency_ms, error_code) "
            "VALUES (:id, :trace_id, :tenant_id, :conversation_id, :agent_id, :agent_version, "
            " :prompt_version, :execution_mode, :status, :input_tokens, :output_tokens, "
            " :cost_usd, :cache_hit, :latency_ms, :error_code)"
        ),
        {
            "id": run_id,
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "agent_version": agent_version,
            "prompt_version": prompt_version,
            "execution_mode": execution_mode,
            "status": status,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "cache_hit": cache_hit,
            "latency_ms": latency_ms,
            "error_code": error_code,
        },
    )
