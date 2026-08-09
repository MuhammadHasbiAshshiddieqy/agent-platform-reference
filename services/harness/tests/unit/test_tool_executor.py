"""§5.3's `act` + §8.4's preview -> (approval branch) -> execute flow,
exercised against `FakeBusinessApiClient` (the real contract shapes are
proven separately by services/mock-business-api/tests/integration/
test_contract.py — these tests are about `executor.py`'s own branching:
§22.4's second authorization check, data_scope enforcement, risk-based
approval branching, and error handling). `allowed_tools`/`scope_
constraints` are built by hand per scenario here rather than through a
real `PolicyResolver.resolve()` call — `test_yaml_policy_resolver.py` and
`tests/conformance/test_policy_resolver.py` own proving the resolver
itself; this file assumes its output and tests what `execute_tool_call`
does with it.
"""

from __future__ import annotations

import pytest
from _helpers import (
    FakeBusinessApiClient,
    FakeRetrievalClient,
    FakeTokenExchangeClient,
    real_tool_manifests,
)
from contracts.authz import ScopeConstraint
from contracts.common import DataScope
from harness.graph.state import ToolCallState
from harness.tools.executor import execute_tool_call

TOOL_MANIFESTS = real_tool_manifests()


def _call(name: str, **arguments: object) -> ToolCallState:
    return ToolCallState(id="call_1", name=name, arguments=arguments)


def _self_scope(employee_id: str) -> ScopeConstraint:
    return ScopeConstraint(
        data_scope=DataScope.SELF, scope_param="employee_id", allowed_ids=[employee_id]
    )


async def _execute(
    call: ToolCallState,
    *,
    employee_id: str | None = "emp_001",
    user_id: str = "usr_budi",
    allowed_tools: list[str],
    scope_constraints: dict[str, ScopeConstraint] | None = None,
    business_api: FakeBusinessApiClient | None = None,
    retrieval: FakeRetrievalClient | None = None,
    token_exchange: FakeTokenExchangeClient | None = None,
) -> object:
    return await execute_tool_call(
        call,
        tenant_id="tnt_demo",
        user_id=user_id,
        employee_id=employee_id,
        trace_id="trc_test",
        subject_token="subject-token-test",
        tool_manifests=TOOL_MANIFESTS,
        allowed_tools=allowed_tools,
        scope_constraints=scope_constraints or {},
        business_api=business_api or FakeBusinessApiClient(),  # type: ignore[arg-type]
        retrieval=retrieval or FakeRetrievalClient(),  # type: ignore[arg-type]
        token_exchange=token_exchange or FakeTokenExchangeClient(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_unknown_tool_returns_an_error_outcome() -> None:
    outcome = await _execute(_call("delete_everything"), allowed_tools=[])
    assert outcome.invocation.status == "error"  # type: ignore[attr-defined]
    assert "unknown tool" in outcome.content  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_tool_not_in_allowed_tools_is_rejected() -> None:
    # §22.4's second check — the tool exists (it's a real manifest) but
    # wasn't in what PolicyResolver authorized for this run.
    business_api = FakeBusinessApiClient()
    outcome = await _execute(
        _call("submit_leave_request", employee_id="emp_001", leave_days=2, start_date="2026-09-01"),
        allowed_tools=["get_leave_balance"],  # submit_leave_request deliberately absent
        business_api=business_api,
    )
    assert outcome.invocation.status == "error"  # type: ignore[attr-defined]
    assert "not authorized" in outcome.content  # type: ignore[attr-defined]
    assert business_api.previews == []


@pytest.mark.asyncio
async def test_self_scope_violation_is_rejected_not_silently_forced() -> None:
    # Model passes someone else's employee_id — §22.9 test class 3 wants
    # this REJECTED, not silently corrected (M5's original behavior).
    business_api = FakeBusinessApiClient()
    constraint = _self_scope("emp_001")
    outcome = await _execute(
        _call("get_leave_balance", employee_id="emp_999"),
        allowed_tools=["get_leave_balance"],
        scope_constraints={"get_leave_balance": constraint},
        business_api=business_api,
    )
    assert outcome.invocation.status == "error"  # type: ignore[attr-defined]
    assert business_api.queries == []


@pytest.mark.asyncio
async def test_self_scope_defaults_to_caller_when_model_omits_it() -> None:
    business_api = FakeBusinessApiClient(
        query_result={"employee_id": "emp_001", "leave_balance": 8}
    )
    constraint = _self_scope("emp_001")
    outcome = await _execute(
        _call("get_leave_balance"),  # no employee_id at all
        allowed_tools=["get_leave_balance"],
        scope_constraints={"get_leave_balance": constraint},
        business_api=business_api,
    )
    assert outcome.invocation.status == "ok"  # type: ignore[attr-defined]
    assert business_api.queries[0]["params"]["employee_id"] == "emp_001"


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_before_calling_business_api() -> None:
    business_api = FakeBusinessApiClient()
    outcome = await _execute(
        _call("submit_leave_request", employee_id="emp_001"),  # missing leave_days/start_date
        allowed_tools=["submit_leave_request"],
        business_api=business_api,
    )
    assert outcome.invocation.status == "error"  # type: ignore[attr-defined]
    assert business_api.previews == []


@pytest.mark.asyncio
async def test_mutation_requiring_approval_creates_pending_approval_not_execute() -> None:
    business_api = FakeBusinessApiClient(risk_level="high", requires_approval=True)
    constraint = _self_scope("emp_001")
    outcome = await _execute(
        _call("submit_leave_request", employee_id="emp_001", leave_days=7, start_date="2026-09-01"),
        allowed_tools=["submit_leave_request"],
        scope_constraints={"submit_leave_request": constraint},
        business_api=business_api,
    )
    assert outcome.mutation_previewed == "submit_leave_request"  # type: ignore[attr-defined]
    assert outcome.mutation_executed is None  # type: ignore[attr-defined]
    assert business_api.executes == []  # never called — that's the approval endpoint's job
    assert outcome.pending_approval is not None  # type: ignore[attr-defined]
    assert outcome.pending_approval.risk_level == "high"  # type: ignore[attr-defined]
    assert outcome.mutation_request is not None  # type: ignore[attr-defined]
    assert outcome.mutation_request.status == "awaiting_approval"  # type: ignore[attr-defined]
    assert outcome.mutation_request.approval_id == outcome.pending_approval.approval_id  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_mutation_not_requiring_approval_executes_immediately_and_exchanges_a_token() -> None:
    business_api = FakeBusinessApiClient(
        risk_level="medium", requires_approval=False, business_ref="lvr_abc"
    )
    token_exchange = FakeTokenExchangeClient()
    constraint = _self_scope("emp_001")
    outcome = await _execute(
        _call("submit_leave_request", employee_id="emp_001", leave_days=2, start_date="2026-09-01"),
        allowed_tools=["submit_leave_request"],
        scope_constraints={"submit_leave_request": constraint},
        business_api=business_api,
        token_exchange=token_exchange,
    )
    assert outcome.mutation_previewed == "submit_leave_request"  # type: ignore[attr-defined]
    assert outcome.mutation_executed == "submit_leave_request"  # type: ignore[attr-defined]
    assert len(business_api.executes) == 1
    assert outcome.pending_approval is None  # type: ignore[attr-defined]
    assert outcome.mutation_request is not None  # type: ignore[attr-defined]
    assert outcome.mutation_request.status == "executed"  # type: ignore[attr-defined]
    assert outcome.mutation_request.business_ref == "lvr_abc"  # type: ignore[attr-defined]
    # submit_leave_request.yaml declares required_scopes_for_token_exchange
    # — §22.5's downscoped token must have been requested for `preview`.
    assert len(token_exchange.calls) == 1
    assert token_exchange.calls[0]["scope"] == "leave:write"
    assert business_api.previews[0]["access_token"] == "exchanged-token-test"


@pytest.mark.asyncio
async def test_adjust_payroll_has_no_self_scope_check() -> None:
    business_api = FakeBusinessApiClient(risk_level="high", requires_approval=True)
    await _execute(
        _call("adjust_payroll", employee_id="emp_005", adjustment_percent=5.0, reason="promosi"),
        employee_id="emp_003",  # the actor's own employee_id — must NOT overwrite params
        user_id="usr_andi",
        allowed_tools=["adjust_payroll"],
        business_api=business_api,
    )
    assert business_api.previews[0]["params"]["employee_id"] == "emp_005"


@pytest.mark.asyncio
async def test_search_public_faq_routes_to_retrieval_not_business_api() -> None:
    business_api = FakeBusinessApiClient()
    retrieval = FakeRetrievalClient()
    outcome = await _execute(
        _call("search_public_faq", query="jam kerja"),
        allowed_tools=["search_public_faq"],
        business_api=business_api,
        retrieval=retrieval,
    )
    assert outcome.invocation.status == "ok"  # type: ignore[attr-defined]
    assert business_api.queries == []
    assert business_api.previews == []
