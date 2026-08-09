"""Shapes shared between the public gateway API (§8.1) and the internal
harness API (§8.2): what an agent is asked, what it answers, what it cost.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from contracts.common import StrictModel


class AgentInput(StrictModel):
    """§8.1 request.input. Only `text` exists today; extend the Literal, not the shape."""

    type: Literal["text"] = "text"
    content: str


class RunOptions(StrictModel):
    """§8.1 request.options."""

    stream: bool = False
    max_output_tokens: int = 1024
    allow_mutations: bool = False


class TokenBudget(StrictModel):
    """§8.2 — quota already reserved by agent-gateway (§6) for this run.

    Reconciliation happens in Redis, not here; this is what the harness is
    told it may spend, not the source of truth for what remains tenant-wide.
    """

    pool: Literal["sync", "async"]
    reserved_tokens: int = Field(ge=0)


class Citation(StrictModel):
    document_id: str
    chunk_id: str
    source_uri: str
    score: float


class AgentOutput(StrictModel):
    type: Literal["text"] = "text"
    content: str
    citations: list[Citation] = Field(default_factory=list)


class Usage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)


class PendingApproval(StrictModel):
    """§8.4 — surfaced on /invoke and /jobs responses when a mutation escalated to `high`."""

    approval_id: str
    action_name: str
    risk_level: Literal["high"] = "high"
