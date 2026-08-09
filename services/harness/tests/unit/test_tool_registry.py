"""§22.2's tool manifest loader + §21/ADR-011's audience filter, tested
against the REAL `config/tools/*.yaml` files (via `_helpers.py`'s
`real_tool_manifests`) — the genuine code path, not a hand-rolled fixture
standing in for it. The ADR-011 boot-failure case (an audience with zero
matching tools) is the one scenario that genuinely needs a synthetic
fixture directory, since the real `config/tools/` never reaches that
state.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _helpers import real_tool_manifests
from contracts.common import Audience, RiskLevel
from harness.authz.manifest import ManifestLoadError, load_tool_manifests
from harness.tools.registry import to_openai_schema


def test_internal_audience_loads_every_tool_including_the_dual_audience_one() -> None:
    manifests = real_tool_manifests(audience=Audience.INTERNAL)
    assert set(manifests) == {
        "get_leave_balance",
        "submit_leave_request",
        "adjust_payroll",
        "search_public_faq",
    }


def test_external_audience_loads_only_search_public_faq() -> None:
    # §21/ADR-011's own required assertion: the external deployment's
    # tool set must be exactly this one tool, not "internal minus a few".
    manifests = real_tool_manifests(audience=Audience.EXTERNAL)
    assert set(manifests) == {"search_public_faq"}


def test_manifest_load_fails_boot_when_audience_has_zero_matching_tools(tmp_path: Path) -> None:
    (tmp_path / "internal_only.yaml").write_text(
        "name: internal_only_tool\n"
        "version: 1\n"
        "kind: readonly\n"
        "audience: [internal]\n"
        "risk_level: low\n"
        "description_for_model: test\n"
        "parameters_schema: GetLeaveBalanceParams\n"
        "domain: hr\n"
        "business_action: internal_only_tool\n"
        "data_scope: tenant\n"
    )
    with pytest.raises(ManifestLoadError):
        load_tool_manifests(tmp_path, audience=Audience.EXTERNAL)


def test_to_openai_schema_shape() -> None:
    tool = real_tool_manifests(audience=Audience.INTERNAL)["get_leave_balance"]
    schema = to_openai_schema(tool)
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "get_leave_balance"
    assert "employee_id" in schema["function"]["parameters"]["properties"]


def test_approver_permission_for_conditional_escalation() -> None:
    tool = real_tool_manifests(audience=Audience.INTERNAL)["submit_leave_request"]
    assert tool.approver_permission_for(RiskLevel.HIGH) == "leave.request.approve"
    assert tool.approver_permission_for(RiskLevel.MEDIUM) is None


def test_approver_permission_for_static_high_risk() -> None:
    tool = real_tool_manifests(audience=Audience.INTERNAL)["adjust_payroll"]
    assert tool.approver_permission_for(RiskLevel.HIGH) == "payroll.adjust"
