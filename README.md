# agent-platform-reference

Production-grade AI agent platform: per-role RBAC, guardrails, hybrid RAG on pgvector, two-phase mutation contract with human approval, and eval gates in CI. 18 services, one `make demo`, zero cost.

Full spec: [docs/SPEC.md](docs/SPEC.md). Working context for contributors (and Claude Code): [CLAUDE.md](CLAUDE.md).

## Status

Under active implementation, milestone by milestone (§15 of the spec) — this is not yet the finished
demo the spec describes. `make demo` and a walkthrough GIF land once the milestone list below reaches M7.

| Milestone | State | What exists |
|---|---|---|
| M0 — Foundation | ✅ done | `packages/contracts`, Postgres schemas + RLS (Alembic), infra `docker compose` stack |
| M1 — Minimal sync path | ✅ done | `model-router`, `agent-harness` (LangGraph, 1 node), `agent-gateway`, Kong+JWT — a JWT through Kong reaches an LLM and comes back with a matching trace in Langfuse |
| M2 — Quota & rate limiting | ✅ done | Kong `rate-limiting` (L1) + Redis token-bucket reserve/reconcile with an expired-reservation sweeper (L2, `gateway/quota.py`) — a tenant over quota gets `429` + `Retry-After`, other tenants are unaffected, reconciliation leaves the bucket at exactly the tokens actually used |
| M3 — RAG | ✅ done | `services/ingestion` (filesystem → chunk → embed → upsert, incremental content-hash sync, tombstoning), `services/retrieval` (§28.9 hybrid dense+sparse RRF search, Infinity rerank, ACL filtering), `agent-harness` `retrieve` node — a question answerable from an ingested doc gets a grounded answer with valid citations, and ACL (`grp_hr`) is enforced end-to-end through Kong |
| M4 — Guardrails | ✅ done | `agent-harness` `guardrails/` package — §9.1 input (size, Presidio PII redaction w/ custom Indonesian recognizers, prompt injection, off-topic) and §9.2 output (format validity, groundedness, PII leak redaction, YAML policy rules) checks wired into the LangGraph loop, every decision logged to `audit.guardrail_events` + `guardrail_events_total`; a broken guardrail check fails closed (`503`), never open |
| M5 — Tool-calling & mutations | ✅ done | `services/mock-business-api` (seed-backed HR/payroll actions, own auth/preview-token/idempotency), `agent-harness` `act` node — a bounded `respond`↔`act` tool-calling loop (§5.3/§8.4's two-phase `preview`→`execute` contract): readonly tools answer directly, `risk_level: high` mutations (e.g. >5 days' leave) stop at `awaiting_approval` and require a human decision via `POST /v1/approvals/{id}/decision`, replayed decisions execute exactly once (§23.2i) |
| M5b — RBAC & tool authorization | ✅ done | `services/mock-idp` (dev login + RFC 8693 token exchange), `agent-harness` `authz/` — §22.1's five-set intersection (`agent_profile.allowed_tools ∩ HARNESS_AUDIENCE-filtered manifest ∩ permissions ∩ allow_mutations ∩ ¬killswitch`) as a swappable `PolicyResolver` (ADR-008), computed once per run by a new `authorize` node and logged to `audit.authz_decisions`; a disallowed tool never reaches the model's schema (leakage-tested against the actual outgoing payload) and a `data_scope: self` violation is rejected, not silently corrected; `HARNESS_AUDIENCE=external` boots with only one deliberately public tool and sits on a Docker network that structurally cannot resolve `mock-business-api` (§21/ADR-011, proven via DNS lookup); `POST /v1/admin/killswitch/{tools\|agents}/{name}` disables a tool in seconds via Redis |
| M6 — Async path | ✅ done | `services/async-worker` (new — a job *executor*, calling `agent-harness`'s own `/internal/v1/runs`, never a second orchestrator), `POST /v1/agent/jobs`/`GET /v1/agent/jobs/{id}` (gateway) — §5.9's RabbitMQ topology (priority queues, zero-plugin backoff-ladder retry via a no-consumer holding queue, explicit DLQ routing on every dead-letter path), race-safe job claiming (§23.2f), a genuinely separate `async-pool` LiteLLM budget key (§6 L3) threaded into the real model call, and HMAC-signed webhook delivery on completion |
| M7 — Semantic cache | ✅ done | `agent-harness` `cache/` — Redis (`redis-stack-server`) vector KNN lookup keyed on `semcache:{tenant_id}:{agent_id}:{acl_hash}:{prompt_version}`, `acl_hash = sha256(sorted(acl_group_ids))`; a hit returns in zero LLM cost, a miss writes back only when every §10 condition holds (agent+tool `cacheable: true`, no mutation/personal-tool call, no guardrail flag or PII event); event-driven invalidation from `services/ingestion` on document change. Live-verified: cross-user hits sharing one ACL namespace, a differing ACL group missing the identical question (§26.2's "most important assertion in the whole demo"), a personal query never cached, and a real document edit invalidating its cached answer |
| M8 — Evaluation | ✅ done | `services/eval` — a CLI tool (`uv run python -m eval_service.{run,gate,report}`), never its own container. Six deterministic metrics (§13.3, no LLM — tool selection, mutation safety, citation validity, refusal appropriateness, capability leak, PII leakage) gate on every run; four Ragas-judged metrics (faithfulness, answer relevancy, context precision/recall) gate statistically (median-of-k, fixed-seed bootstrap CI vs. a stored baseline, absolute-floor tripwire). `_eval` debug bundle attached to responses only for `EVAL_TENANT_IDS` tenants via a dedicated `mock-idp` impersonation endpoint. A judge failure degrades gracefully per-item rather than crashing the run. Live-verified: a full 16-item smoke run correctly blocked on real findings, with the two zero-tolerance security metrics (mutation safety, PII leakage) passing cleanly; gating the same run twice produced a byte-identical verdict |
| M9 — Production hardening | not started | |

Quickstart (M0+M1+M2+M3+M4+M5+M5b+M6+M7+M8):

```bash
git clone <repo>
cd agent-platform-reference
cp .env.example .env        # fill in secrets — see CLAUDE.md
make up                     # docker compose up -d, waits for healthy
make migrate                # alembic upgrade head
curl -X POST http://localhost:8083/internal/v1/ingest/tnt_demo   # load the seed corpus (§26.1)
make test                   # everything, including live integration tests against the stack
```
