"""Pure-logic test: response parsing shouldn't need a live model-router."""

from __future__ import annotations

import json

import httpx
import pytest
from harness.clients.model_router import ModelRouterClient


@pytest.mark.asyncio
async def test_chat_parses_content_usage_and_cost_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content) == {
            "model": "agent-primary",
            "messages": [{"role": "user", "content": "hi"}],
        }
        return httpx.Response(
            200,
            headers={"x-litellm-response-cost": "0.0042"},
            json={
                "choices": [{"message": {"content": "hello there"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    client = ModelRouterClient("http://model-router:4000", "test-key")
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://model-router:4000",
        headers={"Authorization": "Bearer test-key"},
    )

    result = await client.chat("agent-primary", [{"role": "user", "content": "hi"}])

    assert result.content == "hello there"
    assert result.input_tokens == 5
    assert result.output_tokens == 2
    assert result.cost_usd == pytest.approx(0.0042)

    await client.aclose()


@pytest.mark.asyncio
async def test_chat_defaults_cost_to_zero_when_header_absent() -> None:
    """Local Ollama calls have no dollar cost — absence of the header is
    the normal case there, not something to error on."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client = ModelRouterClient("http://model-router:4000", "test-key")
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://model-router:4000"
    )

    result = await client.chat("agent-local", [{"role": "user", "content": "hi"}])
    assert result.cost_usd == 0.0

    await client.aclose()
