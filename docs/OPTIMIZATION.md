# Optimization notes (advisory, not scheduled)

This is written up from a design discussion, not a plan — nothing here is scheduled work, and none
of it is required by any milestone's Definition of Done. M9 (§15 of [docs/SPEC.md](SPEC.md),
"Production hardening") is the only milestone that touches production-readiness, and its actual
scope is circuit breakers,
backpressure, graceful shutdown, a load test (p95 < 4s @ 50 RPS sync, §23.4), chaos testing, and
Helm manifests — language choice isn't part of it. These are suggestions for *if* this reference
implementation were heading toward a real production deployment, kept here so the reasoning isn't
lost between now and whenever (if ever) that question comes up again.

## The question: should non-harness services move to Rust/Go?

Every service in this stack outside `agent-harness` and `services/eval` is thin, I/O-bound
orchestration — `agent-gateway` proxies and checks Redis/Postgres, `retrieval-service` builds one
SQL query and calls Infinity, `services/ingestion` parses files and calls model-router,
`async-worker` waits on RabbitMQ and calls harness. None of them do CPU-bound work themselves; the
actual compute happens inside Postgres/pgvector, Infinity, or the LLM behind model-router.

That matters for the answer in two different ways:

- **Latency**: rewriting any of these in Rust/Go would not meaningfully reduce end-to-end latency.
  A `respond` LLM call already takes hundreds of milliseconds to multiple seconds (worse on this
  repo's documented CPU-only Ollama fallback — see CLAUDE.md's known quirks); Python's interpreter
  overhead on a thin proxy layer is noise by comparison. This is not the argument for switching.
- **Memory footprint**: this *is* a real argument. A Python process (FastAPI/uvicorn + its
  dependency tree) costs meaningfully more resident memory per replica than an equivalent compiled
  Go or Rust binary, and this repo's whole documented history (CLAUDE.md's "Known environment
  quirks" section) is a chronicle of a 7.75GB Docker VM being exactly at its ceiling. In a real
  production deployment that wants to run several replicas of a stateless service for either
  throughput or availability, footprint-per-replica directly sets how many replicas fit per node —
  a genuine cost lever, not a micro-optimization.

## Ranked candidates

| Service | Priority | Why |
|---|---|---|
| `agent-gateway` | **Highest** | The single public entrypoint — the one service that has to scale horizontally the most under real traffic. Purely thin logic (JWT verify, idempotency check, quota reservation via a Redis Lua script, proxy). This is exactly the shape Go's goroutines or Rust's async runtime are built for: cheap per-connection cost at high concurrency, vs. a Python worker process's much heavier per-request footprint. |
| `async-worker` | Second | Same I/O-bound shape (wait on RabbitMQ, call harness, wait on Postgres). Production job throughput usually wants *many cheap replicas* rather than a few fat ones — footprint reduction compounds per replica here more than almost anywhere else in the stack. |
| `retrieval-service` | Worth it, lower priority | Thin too (Postgres/pgvector and Infinity do the real compute), but it's not the QPS bottleneck `agent-gateway` is — every retrieval call is already gated behind one `agent-harness` `/invoke`, so replica count scales with harness's needs, not directly with public traffic. |
| `services/ingestion` | Skip, or last | Not a hot path — it runs on trigger/schedule, not per user-request. Idle memory isn't being wasted the way N always-on `agent-gateway` replicas would waste it. |

## What should never move

- **`agent-harness`** — stays Python. LangGraph is the actual point of the service (§5.3/§28.5's
  ADR-004), and there is no equivalent orchestration ecosystem in Go or Rust worth chasing for a
  service whose bottleneck is LLM latency anyway, not host-language overhead.
- **`mock-idp` / `mock-business-api`** — not rewrite candidates at all. In a real deployment these
  aren't *ported*, they're **replaced**: `mock-idp` by a real IdP (ADR-009's whole point is that the
  IdP is the one source of truth, `mock-idp` is only a POC stand-in), `mock-business-api` by a real
  HRIS/payroll integration. Optimizing code that exists only to be deleted later is wasted effort.
- **`services/eval`** — stays a Python CLI. It's bound to the Ragas/LangChain ecosystem (§13.4),
  runs on-demand rather than as a long-lived service, and was explicitly out of scope for this
  discussion in the first place.
- **Kong** — already a compiled system (OpenResty/Lua on nginx). Nothing to rewrite; it's
  infrastructure this repo configures, not code this repo owns.

## De-risking the real cost: shared contracts

The actual tradeoff isn't performance, it's losing `packages/contracts` as the single shared
Pydantic source of truth (boundary #1, `CLAUDE.md`) — a Go or Rust service can't `import contracts`
the way every Python service in this repo currently must. Hand-porting every request/response type
into a second language reintroduces exactly the cross-service drift risk `lint-imports` currently
makes structurally impossible.

The mitigation is codegen, not hand-porting: every `contracts` model already has
`.model_json_schema()` available for free (it's a `pydantic.BaseModel`). Emitting that schema and
running it through `quicktype` or `openapi-generator` produces Go structs / Rust structs
mechanically, on every contract change, the same way a `.proto` file would in a gRPC setup — schema
drift becomes a regenerate-and-recompile step, not a manual sync a reviewer has to catch by hand.

## When this would actually be worth doing

Not before real numbers exist. This repo's own M9 defines the first real load test (p95 < 4s @
50 RPS sync, §23.4) — that's the point where "is `agent-gateway`'s memory footprint actually a
constraint" stops being a guess and becomes something profiled under realistic concurrent load. If
and when it's worth doing:

- **One service at a time**, starting with `agent-gateway` per the ranking above — never a
  big-bang rewrite of the whole non-harness surface at once.
- **Keep the HTTP/JSON contract identical** so the replacement is a drop-in behind Kong — nothing
  about §21's audience routing, §22's authz flow, or any other service's expectations should need
  to change just because one box in the topology diagram changed language.
