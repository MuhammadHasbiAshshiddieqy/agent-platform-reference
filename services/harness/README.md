# agent-harness

Orchestrates the agent loop (§5.3). M1 scope: one LangGraph node
(`respond`) that calls `model-router`, persists the run to
`conversation.*`, and sends a trace to Langfuse. No RAG, no tool calling,
no guardrails yet — those land at M3/M5/M4.

## Run standalone

Needs the M0 infra stack up (`make up` from repo root) plus a running
`model-router` (M1) with `LANGFUSE_INIT_*` provisioning applied.

```bash
uv run --package harness uvicorn harness.main:app --reload --port 8081
```

Required env vars: see `harness.config.Settings` — `DATABASE_URL`
(`agent_app`, not `agent` — see root `CLAUDE.md`), `MODEL_ROUTER_URL`,
`MODEL_ROUTER_KEY`, `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`.

## Test

```bash
uv run pytest services/harness/tests
```
