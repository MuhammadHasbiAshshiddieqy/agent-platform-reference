"""§8.1 — agent-gateway public API. The only shapes external callers see."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from contracts.agent import AgentInput, AgentOutput, PendingApproval, RunOptions, Usage
from contracts.common import JobPriority, JobStatus, StrictModel
from contracts.eval import EvalDebugBundle


class InvokeContext(StrictModel):
    """§8.1 request.context — optional hints from the calling application."""

    locale: str | None = None
    channel: str | None = None


class AgentInvokeRequest(StrictModel):
    """POST /v1/agent/invoke — synchronous."""

    conversation_id: str | None = None
    agent_id: str
    input: AgentInput
    context: InvokeContext = Field(default_factory=InvokeContext)
    options: RunOptions = Field(default_factory=RunOptions)


class AgentInvokeResponse(StrictModel):
    run_id: str
    conversation_id: str
    trace_id: str
    output: AgentOutput
    usage: Usage
    degraded: list[str] = Field(default_factory=list)
    pending_approvals: list[PendingApproval] = Field(default_factory=list)
    # §10 — a cache hit skips retrieval/LLM entirely; exposed to the
    # caller (not just an internal metric) so it doesn't have to infer
    # "was this cached" from wall-clock latency alone, which is noisy
    # under real network/CPU conditions.
    cache_hit: bool = False
    # §13.1 — only ever populated when harness honored `X-Eval-Mode`
    # (tenant_id ∈ EVAL_TENANT_IDS); `None` for every normal request,
    # including one that requested eval mode but wasn't eligible.
    eval: EvalDebugBundle | None = Field(default=None, alias="_eval")


class AgentJobRequest(AgentInvokeRequest):
    """POST /v1/agent/jobs — same body as /invoke, plus async-only fields."""

    priority: JobPriority = JobPriority.STANDARD
    callback_url: str | None = None
    callback_secret_ref: str | None = None


class AgentJobAcceptedResponse(StrictModel):
    job_id: str
    status: Literal["queued"] = "queued"
    trace_id: str
    poll_url: str


class JobError(StrictModel):
    code: str
    message: str
    retriable: bool


class AgentJobStatusResponse(StrictModel):
    """GET /v1/agent/jobs/{job_id}."""

    job_id: str
    status: JobStatus
    attempts: int = Field(ge=0)
    result: AgentInvokeResponse | None = None
    error: JobError | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ApprovalDecisionRequest(StrictModel):
    """POST /v1/approvals/{approval_id}/decision."""

    decision: Literal["approve", "reject"]


class ApprovalDecisionResponse(StrictModel):
    approval_id: str
    status: Literal["approved", "rejected"]
    # audit.mutation_requests.status after this decision — "executed" | "rejected" | "failed"
    mutation_status: str
    business_ref: str | None = None
