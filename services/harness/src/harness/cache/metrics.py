"""§12.2's `semantic_cache_requests_total{result}` counter."""

from __future__ import annotations

from prometheus_client import Counter

semantic_cache_requests_total = Counter(
    "semantic_cache_requests_total",
    "§12.2 — every cache lookup outcome",
    ["result"],  # hit | miss | skip
)
