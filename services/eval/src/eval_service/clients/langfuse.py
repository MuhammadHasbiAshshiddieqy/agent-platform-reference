"""§13.9's nightly-tier production sampling — reads real traces back out
of Langfuse instead of querying its ClickHouse store directly. ClickHouse
is Langfuse's own internal schema (never a stable contract to build
against, can change on any Langfuse upgrade); the public API/SDK is the
one thing Langfuse promises not to break. `services/harness`'s own
`observability/tracing.py` is the writer side of this same client — same
project, same credentials, just read instead of write.

The Langfuse Python SDK's query methods (`fetch_traces`/`fetch_trace`)
are synchronous; every other client in this package is async, so calls
are wrapped in `asyncio.to_thread` rather than mixing sync/async call
styles at the caller.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass, field

from langfuse import Langfuse


@dataclass
class CitedChunk:
    chunk_id: str
    source_uri: str
    content: str


@dataclass
class ProductionTrace:
    trace_id: str
    question: str
    answer: str
    timestamp: dt.datetime
    cited_chunks: list[CitedChunk] = field(default_factory=list)


def _as_text(value: object) -> str:
    """Trace `input`/`output` are typed `Any` in the SDK — this project's
    own writer (`tracing.py`) always sends plain strings, but a trace
    from some other source (or a Langfuse version that wraps it) might
    not. Coerce rather than crash the whole sampling run over one odd
    trace."""
    return value if isinstance(value, str) else str(value)


class LangfuseClient:
    def __init__(self, *, public_key: str, secret_key: str, host: str) -> None:
        if not public_key or not secret_key:
            raise ValueError(
                "LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY are required for production "
                "trace sampling (§13.9) — unlike eval-smoke/eval-full, this is not "
                "optional here since there is no other source for real trace data."
            )
        self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    async def fetch_recent_traces(
        self, *, agent_id: str, since: dt.datetime, limit: int
    ) -> list[ProductionTrace]:
        """Traces tagged `agent:{agent_id}` (written by `tracing.py`'s
        `record_run`, §12.1) with a timestamp on or after `since`. A
        cache-hit trace (no `llm_call_1` generation, §10 step 4) is
        included like any other — its `output` is still a real answer
        worth scoring, just served without a fresh LLM call.
        """
        response = await asyncio.to_thread(
            self._client.fetch_traces,
            tags=f"agent:{agent_id}",
            from_timestamp=since,
            limit=limit,
            order_by="timestamp.desc",
        )

        traces: list[ProductionTrace] = []
        for trace in response.data:
            if trace.input is None or trace.output is None:
                # A trace without both (e.g. still in flight, or an
                # instrumentation gap from before this metadata existed)
                # has nothing scoreable — skip rather than fail the batch.
                continue
            metadata = trace.metadata if isinstance(trace.metadata, dict) else {}
            cited_chunks = [
                CitedChunk(
                    chunk_id=c.get("chunk_id", ""),
                    source_uri=c.get("source_uri", ""),
                    content=c.get("content", ""),
                )
                for c in metadata.get("cited_chunks", [])
                if isinstance(c, dict)
            ]
            traces.append(
                ProductionTrace(
                    trace_id=trace.id,
                    question=_as_text(trace.input),
                    answer=_as_text(trace.output),
                    timestamp=trace.timestamp,
                    cited_chunks=cited_chunks,
                )
            )
        return traces
