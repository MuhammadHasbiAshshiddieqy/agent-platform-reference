# mock-business-api

§8.4's mutation contract (two-phase preview → execute) plus §24's mock
domain adapter: one container serving `/hr/v1/` and `/payroll/v1/` as two
independent domain adapters (§5.12 — real ownership boundary, compacted
container count for the POC). Checks its own authorization independently
of harness (§8.4: "jangan pernah mempercayai bahwa harness sudah
mengecek"), reading `seed/users.yaml` directly. State is in-memory,
seeded from `seed/business_state.json` at startup — sanctioned by §24,
since only the contract matters here, not real payroll persistence.

Three actions (§24.2): `get_leave_balance` (readonly), `submit_leave_request`
(mutation, escalates to `risk_level: high` above 5 leave days),
`adjust_payroll` (mutation, always `risk_level: high`).

`X-Simulate` header (§24.3) injects failures: `timeout`, `error_500`,
`rate_limit`, `partial_failure` (preview succeeds, execute fails).

## Run standalone

```bash
uv run --package mock-business-api uvicorn mock_business_api.main:app --reload --port 8084
```

Required env vars: see `mock_business_api.config.Settings` — defaults
point at `/app/seed/...`; override `BUSINESS_STATE_PATH`/`USERS_PATH` for
local runs outside Docker.

## Test

```bash
uv run pytest services/mock-business-api/tests
```
