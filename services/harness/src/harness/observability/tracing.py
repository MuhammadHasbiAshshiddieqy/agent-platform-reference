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
) -> None:
    trace = langfuse.trace(
        id=trace_id,
        name="agent_run",
        user_id=user_id,
        metadata={
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "execution_mode": execution_mode,
            "cache_hit": cache_hit,
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
