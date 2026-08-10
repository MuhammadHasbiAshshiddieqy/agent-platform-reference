# Optimization notes (advisory, not scheduled)

This is written up from design discussions, not a plan — nothing here is scheduled work, and none
of it is required by any milestone's Definition of Done. M9 (§15 of [docs/SPEC.md](SPEC.md),
"Production hardening") is the only milestone that touches production-readiness, and its actual
scope is circuit breakers, backpressure, graceful shutdown, a load test (p95 < 4s @ 50 RPS sync,
§23.4), chaos testing, and Helm manifests — neither of the topics below is part of it. These are
suggestions for *if* this reference implementation were heading toward a real production
deployment, kept here so the reasoning isn't lost between now and whenever (if ever) either
question comes up again.

## Part 1 — Service language & runtime footprint

### The question: should non-harness services move to Rust/Go?

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

### Ranked candidates

| Service | Priority | Why |
|---|---|---|
| `agent-gateway` | **Highest** | The single public entrypoint — the one service that has to scale horizontally the most under real traffic. Purely thin logic (JWT verify, idempotency check, quota reservation via a Redis Lua script, proxy). This is exactly the shape Go's goroutines or Rust's async runtime are built for: cheap per-connection cost at high concurrency, vs. a Python worker process's much heavier per-request footprint. |
| `async-worker` | Second | Same I/O-bound shape (wait on RabbitMQ, call harness, wait on Postgres). Production job throughput usually wants *many cheap replicas* rather than a few fat ones — footprint reduction compounds per replica here more than almost anywhere else in the stack. |
| `retrieval-service` | Worth it, lower priority | Thin too (Postgres/pgvector and Infinity do the real compute), but it's not the QPS bottleneck `agent-gateway` is — every retrieval call is already gated behind one `agent-harness` `/invoke`, so replica count scales with harness's needs, not directly with public traffic. |
| `services/ingestion` | Skip, or last | Not a hot path — it runs on trigger/schedule, not per user-request. Idle memory isn't being wasted the way N always-on `agent-gateway` replicas would waste it. |

### What should never move

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

### De-risking the real cost: shared contracts

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

### When this would actually be worth doing

Not before real numbers exist. This repo's own M9 defines the first real load test (p95 < 4s @
50 RPS sync, §23.4) — that's the point where "is `agent-gateway`'s memory footprint actually a
constraint" stops being a guess and becomes something profiled under realistic concurrent load. If
and when it's worth doing:

- **One service at a time**, starting with `agent-gateway` per the ranking above — never a
  big-bang rewrite of the whole non-harness surface at once.
- **Keep the HTTP/JSON contract identical** so the replacement is a drop-in behind Kong — nothing
  about §21's audience routing, §22's authz flow, or any other service's expectations should need
  to change just because one box in the topology diagram changed language.

## Part 2 — Retrieval quality: sparse search ranking

### The question: is Postgres `tsvector`/GIN good enough for the sparse side of hybrid search?

§28.9's hybrid search (`services/retrieval/src/retrieval/persistence/search.py`, the `_HYBRID_QUERY`
CTE) fuses two candidate lists via Reciprocal Rank Fusion: a dense side (`pgvector`'s `<=>` cosine
distance) and a sparse side, which is where this question lives. The sparse side comes from
`catalog.chunks.content_tsv` — a generated column, `tsvector GENERATED ALWAYS AS
(to_tsvector('simple', content)) STORED` (migration `0005_catalog.py`) — indexed with
`CREATE INDEX ... USING gin (content_tsv)`, and ranked with
`ts_rank_cd(c.content_tsv, q)` where `q = plainto_tsquery('simple', :qtext)`.

This is real, working full-text search — GIN is the correct index type for a `tsvector` column, and
`ts_rank_cd` is a legitimate Postgres ranking function. But it is not BM25, and the gap is two
separate, compounding weaknesses:

1. **Ranking function.** `ts_rank_cd` ("cover density" ranking) has no `k1` (term-frequency
   saturation) or `b` (document-length normalization) parameters the way real BM25 does. Worse, the
   current query doesn't pass `ts_rank_cd`'s own optional `normalization` bitmask argument at all,
   so chunks are scored with **zero length normalization** — a long chunk that happens to repeat a
   query term stacks score roughly linearly, in exactly the way BM25's saturation curve exists to
   prevent. This is the textbook gap between "Postgres has full-text search" and "Postgres has a
   real IR ranking function" — and it's precisely why RRF (which only needs each side's *rank
   order*, never comparable score magnitudes) was the right fusion strategy here rather than trying
   to combine raw dense-cosine and sparse-`ts_rank_cd` scores directly.

2. **Tokenization.** `to_tsvector('simple', ...)` uses Postgres's `simple` text search
   configuration — no stemming, no stopword removal, in any language. Postgres ships no built-in
   Indonesian configuration, so `simple` was the safe choice over mismatching an English stemmer
   against Indonesian content — but it also means "cuti" and "pengambilan cuti" only overlap on
   exact token match, and every Indonesian function word ("yang", "untuk", "dengan", "dan") sits in
   the index at full weight, diluting relevance scoring further. This compounds the ranking gap: the
   sparse side's *recall* is already narrower than a language-aware tokenizer would give, before
   BM25 vs. `ts_rank_cd` even enters the picture.

### Production options

| Option | What changes | Tradeoff |
|---|---|---|
| **`pg_search` (ParadeDB)** — a Postgres extension exposing a real Tantivy-backed BM25 index (`@@@` operator, configurable `k1`/`b`) | New extension inside the existing `postgres` container/schema; `content_tsv` + its GIN index get replaced by a BM25 index on the same table, `_HYBRID_QUERY`'s sparse CTE swaps `ts_rank_cd` for `paradedb.score(...)` | Smallest architectural delta — no new service, no new schema-ownership question, no second write path off ingestion. In the same spirit as ADR-002's own reasoning for choosing pgvector over Qdrant ("menghapus satu komponen" — stay inside Postgres rather than add infrastructure). Real BM25 plus a real tokenizer library (including reasonable multilingual support) in one move. |
| **Dedicated search engine** (Elasticsearch / OpenSearch / Typesense / Meilisearch) | A new service outside Postgres; `services/ingestion` needs a second write path alongside `catalog.chunks`'s upsert — §5.11's content-hash incremental sync and soft-delete tombstoning both need mirroring into the new index | The most battle-tested BM25 implementation available, plus real language analyzers (including Indonesian) and extra features (typo tolerance, faceted filtering) — but it's a genuinely new moving part: a new failure mode `retrieve`'s degraded-mode handling would need to cover (§5.3's "degrades, doesn't fail closed" logic gets a second dependency), and ACL/tenant filtering (`acl_group_ids &&`, RLS) would need re-implementing at the new engine's query layer since it no longer runs inside Postgres. |
| **Fix tokenization only, keep `ts_rank_cd`** — swap the `simple` config for a custom Postgres text search configuration (a stemmer plus an Indonesian stopword list) | One migration, no new extension, no new service | Cheapest possible partial fix — recovers some recall from the tokenization gap without touching the ranking-function gap at all. Worth doing regardless of which (if any) BM25 path gets chosen, since it's nearly free and orthogonal to the ranking-function decision. |

### Priority

This is a different axis from Part 1 (retrieval *quality*, not runtime footprint) — not really
comparable on the same ranked list, and not urgent at this reference implementation's own scale:
§13.2 notes production golden-set scale is 100-300 items against a handful of demo documents: BM25
vs. `ts_rank_cd` mostly starts to matter once the corpus is large and heterogeneous enough for
document-length normalization to matter, and this repo's is neither yet. If it's ever picked up,
in order: fix the tokenizer configuration first (cheap, isolated, no architecture change and no
regressions to risk), then decide between `pg_search` (if staying inside Postgres is a hard
constraint — it usually should be, per ADR-002's reasoning) and a dedicated engine (only once corpus
size or feature needs — typo tolerance, faceting, cross-language stemming — outgrow what a single
Postgres instance can index well).
