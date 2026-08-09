# agent-gateway

Public entrypoint (§5.2). M1 scope: `POST /v1/agent/invoke` — JWT
re-verification, idempotency, proxy to `agent-harness`. No quota (M2), no
async jobs (M6) yet.

## Run standalone

```bash
uv run --package gateway uvicorn gateway.main:app --reload --port 8080
```

Required env vars: see `gateway.config.Settings` — `DATABASE_URL`
(`agent_app`), `HARNESS_URL`, `JWT_SIGNING_SECRET` (must match Kong's
`config/kong/kong.yml` consumer secret).

## Test

```bash
uv run pytest services/gateway/tests
```
