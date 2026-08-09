"""Shared test doubles — not itself a test module."""

from __future__ import annotations

from async_worker.clients.harness import HarnessError
from contracts.agent import AgentOutput, Usage
from contracts.common import ExecutionMode, JobPriority
from contracts.gateway import AgentInvokeResponse
from contracts.harness import AgentRunRequest
from contracts.jobs import AsyncJobMessage


def make_run_request(*, run_id: str = "job_test", tenant_id: str = "tnt_demo") -> AgentRunRequest:
    from contracts.agent import AgentInput, RunOptions, TokenBudget

    return AgentRunRequest(
        run_id=run_id,
        trace_id="trc_test",
        tenant_id=tenant_id,
        user_id="usr_andi",
        agent_id="hr-assistant",
        input=AgentInput(content="Ringkas kebijakan HR"),
        options=RunOptions(),
        execution_mode=ExecutionMode.ASYNC,
        budget=TokenBudget(pool="async", reserved_tokens=500),
    )


def make_job_message(
    *, job_id: str = "job_test", tenant_id: str = "tnt_demo", attempts: int = 0
) -> AsyncJobMessage:
    return AsyncJobMessage(
        job_id=job_id,
        tenant_id=tenant_id,
        priority=JobPriority.BULK,
        run_request=make_run_request(run_id=job_id, tenant_id=tenant_id),
        callback_url="https://tenant.example.invalid/hooks/agent",
        callback_secret_ref="secret_ref_abc",
        attempts=attempts,
        quota_reservation_key=f"quota:reservation:{tenant_id}:async:{job_id}",
    )


class FakeHarnessClient:
    def __init__(
        self, *, result: AgentInvokeResponse | None = None, error: HarnessError | None = None
    ) -> None:
        self._result = result or AgentInvokeResponse(
            run_id="job_test",
            conversation_id="conv_test",
            trace_id="trc_test",
            output=AgentOutput(content="Ringkasan kebijakan HR..."),
            usage=Usage(input_tokens=100, output_tokens=50, cost_usd=0.0),
        )
        self._error = error
        self.calls: list[AgentRunRequest] = []

    async def run(self, request: AgentRunRequest) -> AgentInvokeResponse:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        return self._result

    async def aclose(self) -> None:
        pass


class FakeQuotaReconciler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def reconcile(self, reservation_key: str, actual_tokens: int) -> None:
        self.calls.append((reservation_key, actual_tokens))


class FakeWebhookSender:
    def __init__(self, *, delivered: bool = True) -> None:
        self._delivered = delivered
        self.calls: list[dict[str, object]] = []

    async def send(self, *, url: str, secret_ref: str | None, payload: object) -> bool:
        self.calls.append({"url": url, "secret_ref": secret_ref, "payload": payload})
        return self._delivered

    async def aclose(self) -> None:
        pass
