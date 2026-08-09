"""§22.4's "pengecekan kedua saat eksekusi" — even though a tool was
already filtered into the schema sent to the model, harness re-checks
`data_scope` when the model actually calls it. The two functions here are
deliberately not "force the value silently" (M5's original, simpler
behavior for the two self-scoped tools) — §22.9's required data_scope
test expects an explicit reject when the model passes a value outside the
caller's scope, not a quiet substitution. A missing value still gets a
helpful default (the common, non-malicious case: the model didn't bother
threading the caller's own id through); an explicit wrong value doesn't.
"""

from __future__ import annotations

from contracts.authz import ScopeConstraint


def apply_scope_default(
    params: dict[str, object], *, scope_param: str, employee_id: str | None
) -> None:
    """Fills in the caller's own id when the model omitted the
    scope_param entirely — usability for the common case, not a security
    control (the explicit-value check below is)."""
    if employee_id and not params.get(scope_param):
        params[scope_param] = employee_id


def check_scope(
    params: dict[str, object], *, scope_param: str, constraint: ScopeConstraint
) -> str | None:
    """Returns a denial reason if `params[scope_param]` is outside
    `constraint.allowed_ids`, else None. `allowed_ids=None` means
    unrestricted (tenant scope, or a scope this layer defers entirely to
    business-api)."""
    if constraint.allowed_ids is None:
        return None
    value = params.get(scope_param)
    if value not in constraint.allowed_ids:
        return (
            f"{scope_param}={value!r} is outside the caller's "
            f"{constraint.data_scope.value} scope for this tool"
        )
    return None
