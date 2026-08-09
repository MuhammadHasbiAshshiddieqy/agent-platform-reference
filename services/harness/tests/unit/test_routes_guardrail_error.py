"""§9.2's hard rule, at the boundary that actually matters to a caller:
a `GuardrailServiceError` raised anywhere inside `run_agent` must reach
the client as HTTP 503, never a 200 with a "best effort" answer. Calls
the route function directly rather than spinning up a real app/lifespan
(no DB, no LangGraph checkpointer needed to prove this).
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

# `harness.api.routes` -> `harness.graph.runner` -> `harness.observability.
# tracing` -> `harness.config.settings`, which is a `pydantic-settings`
# instance built at import time and errors without these env vars — set
# before the import below runs, not via a fixture (fixtures run too late).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("MODEL_ROUTER_KEY", "test-key")
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "test-public")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "test-secret")

import pytest
from contracts.agent import AgentInput, RunOptions, TokenBudget
from contracts.common import ExecutionMode
from contracts.harness import AgentRunRequest
from fastapi import HTTPException
from harness.api.routes import create_run
from harness.guardrails.errors import GuardrailServiceError


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        run_id="run_test",
        trace_id="trc_test",
        tenant_id="tnt_demo",
        user_id="usr_test",
        agent_id="hr-assistant",
        input=AgentInput(content="Berapa sisa cuti saya?"),
        options=RunOptions(),
        execution_mode=ExecutionMode.SYNC,
        budget=TokenBudget(pool="sync", reserved_tokens=100),
    )


@pytest.mark.asyncio
async def test_guardrail_service_error_becomes_http_503(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise(*args: object, **kwargs: object) -> None:
        raise GuardrailServiceError("presidio blew up")

    monkeypatch.setattr("harness.api.routes.run_agent", AsyncMock(side_effect=_raise))

    fake_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(graph=None, langfuse=None, audience="internal"))
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_run(_request(), fake_request)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 503
