"""§12.1 — one trace per agent run, `trace.id` set to our own `trace_id` so
it matches `X-Trace-Id` / the response body exactly (§12.1's hard rule: a
trace that can't be correlated with the HTTP response is useless during an
incident).
"""

from __future__ import annotations

from harness.config import settings
from langfuse import Langfuse


def get_langfuse_client() -> Langfuse:
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )


def record_run(
    langfuse: Langfuse,
    *,
    trace_id: str,
    tenant_id: str,
    user_id: str,
    agent_id: str,
    execution_mode: str,
    model_alias: str,
    input_text: str,
    output_text: str,
    input_tokens: int,
    output_tokens: int,
    cache_hit: bool = False,
    cited_chunks: list[dict[str, str]] | None = None,
) -> None:
    """`cited_chunks` (chunk_id/source_uri/content, one per citation
    actually attached to the response — not every retrieved chunk) rides
    along in trace metadata for every run, not just eval-tagged ones.
    Nothing reads this today except a human in the Langfuse UI, but
    `eval_service.clients.langfuse.fetch_production_traces` (§13.9's
    nightly-tier production sampling) depends on it being here — a
    faithfulness/citation_support judge needs the actual cited text, and
    `contracts.agent.Citation` only carries a chunk_id, never content."""
    trace = langfuse.trace(
        id=trace_id,
        name="agent_run",
        user_id=user_id,
        # Set on the trace itself, not just the child span/generation
        # below — `fetch_trace`/`fetch_traces` (the read side,
        # `eval_service.clients.langfuse`) reads `Trace.input`/`.output`
        # directly and never descends into observations, so leaving these
        # unset here means every production-sampled trace looks empty
        # even though the child span/generation has the same text.
        input=input_text,
        output=output_text,
        # `agent:{agent_id}`/`tenant:{tenant_id}` as tags, not just
        # metadata fields — Langfuse's trace-list API can filter server-
        # side by `tags`, not by arbitrary metadata keys, and §13.9's
        # nightly sampler needs to pull "this agent's traces only"
        # without downloading every trace in the project to filter
        # client-side.
        tags=[f"agent:{agent_id}", f"tenant:{tenant_id}"],
        metadata={
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "execution_mode": execution_mode,
            "cache_hit": cache_hit,
            "cited_chunks": cited_chunks or [],
        },
    )
    if cache_hit:
        # §10 step 4 / §12.1 — a hit still gets a trace (never skipped
        # entirely), just no `llm_call_1` generation span since no LLM
        # call happened for this run.
        trace.span(
            name="cache_lookup", input=input_text, output=output_text, metadata={"hit": True}
        )
    else:
        trace.generation(
            name="llm_call_1",
            model=model_alias,
            input=input_text,
            output=output_text,
            usage={"input": input_tokens, "output": output_tokens, "unit": "TOKENS"},
        )
    langfuse.flush()
