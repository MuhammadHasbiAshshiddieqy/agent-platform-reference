"""§5.4 — the only place harness talks to an LLM, and it never imports a
provider SDK to do it (§4.1 rule 3). This is a plain OpenAI-compatible HTTP
call to LiteLLM, which is the thing actually holding provider credentials.
`model_alias` must be one of the aliases in `config/model-router/config.yaml`
(agent-primary, agent-cheap, agent-local, ...) — never a provider model id.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel


class ToolCallResult(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ChatCompletionResult(BaseModel):
    # None when the model responded with only `tool_calls` (§5.3 `act`) —
    # OpenAI's tool-calling shape allows an assistant message with no text.
    content: str | None
    tool_calls: list[ToolCallResult] | None = None
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ModelRouterClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 60.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,  # harness -> model-router timeout (§14)
        )

    async def chat(
        self,
        model_alias: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        api_key: str | None = None,
    ) -> ChatCompletionResult:
        payload: dict[str, Any] = {"model": model_alias, "messages": messages}
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        # §6 L3 / §5.10 — async-worker's own LiteLLM virtual key
        # (`async-pool`, separate daily USD budget) overrides the
        # instance's default (sync-path) key for exactly this one call,
        # via httpx's per-request header override — never baked into the
        # client at construction, since one harness process serves both
        # sync and async requests from the same compiled graph.
        request_headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        response = await self._client.post(
            "/chat/completions", json=payload, headers=request_headers
        )
        response.raise_for_status()
        body = response.json()
        usage = body.get("usage", {})
        # LiteLLM reports per-call cost in this response header when it can
        # price the provider; local Ollama calls have no dollar cost, so
        # absence of the header is the normal case there, not an error.
        cost_header = response.headers.get("x-litellm-response-cost")
        message = body["choices"][0]["message"]
        return ChatCompletionResult(
            content=message.get("content"),
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cost_usd=float(cost_header) if cost_header is not None else 0.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCallResult] | None:
    if not raw:
        return None
    parsed = []
    for tc in raw:
        try:
            arguments = json.loads(tc["function"]["arguments"] or "{}")
        except (json.JSONDecodeError, KeyError):
            # A weak/local model can emit malformed tool-call JSON. Fail
            # this one call gracefully (empty args -> the tool wrapper's
            # own Pydantic validation will reject it and hand the model a
            # correction message) rather than crashing the whole turn.
            arguments = {}
        parsed.append(ToolCallResult(id=tc["id"], name=tc["function"]["name"], arguments=arguments))
    return parsed
