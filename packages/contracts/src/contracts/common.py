"""Shared primitives: base model config, enums, and header names.

Every other module in this package builds on `StrictModel`. Nothing here
depends on anything outside `contracts` — §4.1 forbids it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for every contract model. Unknown fields are a bug, not a warning."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExecutionMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


class RiskLevel(StrEnum):
    """§8.4 — controls whether a mutation may execute, needs allow_mutations, or needs approval."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolKind(StrEnum):
    READONLY = "readonly"
    MUTATION = "mutation"


class DataScope(StrEnum):
    """§22.2 — row-level authorization tier, orthogonal to required_permissions."""

    SELF = "self"
    TEAM = "team"
    DEPARTMENT = "department"
    TENANT = "tenant"


class Audience(StrEnum):
    """§21 — deployment audience. EXTERNAL must never reach internal-only data."""

    INTERNAL = "internal"
    EXTERNAL = "external"


class JobPriority(StrEnum):
    STANDARD = "standard"
    BULK = "bulk"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class MutationRequestStatus(StrEnum):
    """§8.5 audit.mutation_requests.status lifecycle."""

    PREVIEWED = "previewed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class GuardrailStage(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class GuardrailAction(StrEnum):
    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"
    FLAG = "flag"


class AuthzDecisionType(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ToolInvocationStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class Headers:
    """Header names referenced across §8. Import these — never hardcode the string.

    A typo in a hardcoded header name (`X-Trace-id` vs `X-Trace-Id`) silently
    breaks trace correlation (§12.3) or tenant isolation (§7.3) with no error
    at the call site.
    """

    AUTHORIZATION = "Authorization"
    IDEMPOTENCY_KEY = "Idempotency-Key"
    TRACE_ID = "X-Trace-Id"
    TENANT_ID = "X-Tenant-Id"
    ACTOR_ID = "X-Actor-Id"
    ACTOR_TYPE = "X-Actor-Type"
    INTERNAL_TOKEN = "X-Internal-Token"
    EVAL_MODE = "X-Eval-Mode"
    SIMULATE = "X-Simulate"  # §24.3 mock-business-api failure injection
