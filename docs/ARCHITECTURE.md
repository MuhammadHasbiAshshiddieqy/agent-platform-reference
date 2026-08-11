# Architecture

Visual companion to [docs/SPEC.md](SPEC.md) and [CLAUDE.md](../CLAUDE.md). One combined system
topology diagram, then one flow diagram per major interaction. All diagrams reflect the code as of
M8 (see [README.md](../README.md)'s milestone table) — service names, ports, queue names, and
graph node names are taken directly from `deploy/docker-compose.yml`,
`services/harness/src/harness/graph/build.py`, and `packages/contracts/src/contracts/jobs.py`, not
re-derived from the spec's prose.

## 1. System topology

Every service, every datastore, the message broker, the model backends, and — in the same
diagram, per §21/ADR-011 — the internal/external audience network split. `agent-harness-external`
is not part of the default `make up` stack (`--profile external-test`); it's included here because
it's the thing that makes the audience split a *network* fact, not just a policy one:
`agent-harness-external` is never joined to `hris_internal`, so it cannot resolve
`mock-business-api` at the DNS level, regardless of what any policy engine decides.

The numbers (**①**–**⑥**) mark the one synchronous request's path through the system, in order —
follow those first. Everything else on the diagram (async, identity, ingestion, observability,
eval) is a **side system**, drawn with thin dotted connectors so it never competes with that main
path for attention.

```mermaid
flowchart LR
    classDef startEnd fill:#2f855a,stroke:#1c4532,color:#fff,stroke-width:2px;
    classDef core fill:#2b6cb0,stroke:#1a365d,color:#fff,stroke-width:2px;
    classDef support fill:#718096,stroke:#2d3748,color:#fff;
    classDef store fill:#6b46c1,stroke:#44337a,color:#fff;

    Start(["① Client\nsends request"]):::startEnd
    Kong["② Kong :8000\nJWT verify + rate limit"]:::core
    Gateway["③ agent-gateway :8080\nidempotency + quota"]:::core
    End(["⑥ Client\nreceives response"]):::startEnd

    Start --> Kong --> Gateway

    subgraph HrisNet["hris_internal network — internal audience only"]
        Harness["④ agent-harness :8081\nLangGraph orchestrator\n(see §3 for the node graph)"]:::core
        BAPI["mock-business-api :8084\nHR/payroll: query/preview/execute"]:::support
        Harness --> BAPI
    end
    Gateway --> Harness
    Harness --> End

    subgraph RagModels["⑤ RAG + model backends, called from the graph"]
        direction TB
        Retrieval["retrieval-service :8082\nhybrid search + Infinity rerank"]:::support
        Router["model-router :4000\n→ Gemini / Ollama / Infinity"]:::support
    end
    Harness --> Retrieval
    Harness --> Router

    subgraph ExtNet["agent-harness-external :8091 — profile external-test\nNOT on hris_internal (DNS-proven isolation)"]
        HarnessExt["same image,\nHARNESS_AUDIENCE=external"]:::support
    end
    HarnessExt -->|"search_public_faq only"| Retrieval

    subgraph AsyncLane["side system — async alternative to ②→③→④"]
        direction LR
        Queue["RabbitMQ\nagent.jobs"]:::support
        Worker["async-worker :8085"]:::support
        Queue --> Worker
    end
    Gateway -. "POST /v1/agent/jobs (202)" .-> Queue
    Worker -. "POST /internal/v1/runs" .-> Harness
    Worker -. "webhook, HMAC-signed" .-> Start

    subgraph State["side system — shared state"]
        direction TB
        Postgres[("postgres :5432")]:::store
        Redis[("redis :6379")]:::store
    end
    Gateway -.- State
    Harness -.- State

    subgraph SideSystems["side systems — identity, ingestion, observability, eval"]
        direction TB
        Idp["mock-idp :8087\nlogin / token-exchange"]:::support
        Ingestion["ingestion-service :8083"]:::support
        Obs["langfuse + prometheus/grafana"]:::support
        Eval["services/eval (CLI,\nnever its own container)"]:::support
    end
    Start -.->|"login"| Idp
    Harness -.->|"token exchange\n(mutation preview only)"| Idp
    Ingestion -.->|"cache invalidate"| Harness
    Harness -.->|"traces + /metrics"| Obs
    Eval -.->|"impersonate"| Idp
    Eval -.->|"invoke, X-Eval-Mode: true"| Kong
```

Notes on reading this diagram:

- **Green** = the request's entry/exit. **Blue** = the two services on the request's own critical
  path besides Kong. **Grey** = everything else the graph calls out to. **Purple** = durable state.
- **Solid arrows** trace the one synchronous request, in numbered order. **Dotted arrows** are
  side-band (login, webhooks, traces, metrics, token exchange, async) — the caller doesn't block
  its main response on them.
- `agent-harness` sits in `hris_internal` *and* `default` — it's the only orchestrator allowed to
  reach `mock-business-api`. `agent-harness-external` sits only in `default`; the box around each
  is the actual Docker network boundary, not a drawing convention.
- `services/eval` and the demo `curl` client are both just HTTP clients of the same public surface
  (Kong) — eval-service has no special network access, only a special mock-idp endpoint
  (`/oauth/eval-impersonate`) that itself re-checks the tenant server-side.

## 2. Gateway request lifecycle

Every `/v1/*` call through Kong goes through the same idempotency + quota shape before it ever
reaches harness.

```mermaid
sequenceDiagram
    participant C as Client
    participant K as Kong
    participant G as agent-gateway
    participant R as Redis (db1)
    participant P as Postgres (jobs/conversation)
    participant H as agent-harness

    C->>K: POST /v1/agent/invoke\nAuthorization, Idempotency-Key
    K->>K: verify JWT (L0), rate-limit (L1)
    K->>G: proxy
    G->>G: re-verify JWT independently (§8.4)
    G->>P: INSERT idempotency row (wins the race, §23.2b)
    alt key already used, response stored
        P-->>G: existing response_body
        G-->>C: replay stored response (same status)
    else new request
        G->>R: reserve quota tokens (L2, Lua script)
        alt over quota
            R-->>G: reservation denied
            G-->>C: 429 + Retry-After
        else reserved
            G->>H: POST /internal/v1/runs (proxy)
            H-->>G: AgentInvokeResponse
            G->>R: reconcile reservation to actual usage
            G->>P: store response_body under Idempotency-Key
            G-->>C: 200 AgentInvokeResponse
        end
    end
```

The async twin (`POST /v1/agent/jobs`) is identical through the idempotency step, then reserves
against the **async** quota pool (never `sync`'s — §6) and publishes to RabbitMQ instead of calling
harness directly; §5's job flow diagram below picks up from there.

## 3. Harness: the LangGraph run

One `AgentState` flows through a fixed node graph, computed once per run (`authorize`, never
mid-loop) with a single bounded `respond ↔ act` loop.

```mermaid
flowchart TD
    classDef startEnd fill:#2f855a,stroke:#1c4532,color:#fff,stroke-width:2px;
    classDef node fill:#2b6cb0,stroke:#1a365d,color:#fff;
    classDef loopNode fill:#b7791f,stroke:#7b341e,color:#fff;

    START(("START")):::startEnd --> Authorize["authorize\nfive-set intersection (§3.2 below);\nlogs audit.authz_decisions"]:::node
    Authorize --> InputGR["input_guardrails\nsize → PII redact → injection → off-topic"]:::node
    InputGR -- refuse --> END1(("END\nrefusal")):::startEnd
    InputGR -- continue --> CacheLookup["cache_lookup\nnormalize → embed → RediSearch KNN\nnamespace: semcache:{tenant}:{agent}:{acl_hash}:{prompt_version}"]:::node
    CacheLookup -- hit --> END2(("END\ncache_hit=true, 0 tokens")):::startEnd
    CacheLookup -- miss --> Retrieve["retrieve\nhybrid RRF search, ACL-filtered\n(degrades, doesn't fail closed)"]:::node
    Retrieve --> RagGR["rag_guardrails\nper-chunk injection scan"]:::node
    RagGR --> Respond["respond\noffers tenant/permission-filtered\ntool schema; accumulates tools_offered"]:::loopNode
    Respond -- tool_calls --> Act["act\nexecutor: scope check → query/preview/execute\n(MAX_TOOL_ITERATIONS = 2)"]:::loopNode
    Act --> Respond
    Respond -- final answer --> OutputGR["output_guardrails\nformat validity (1 retry) → groundedness →\nPII leak redact → policy block"]:::node
    OutputGR --> CacheWrite["cache_write\nwrites back only if all §10\neligibility conditions hold"]:::node
    CacheWrite --> END3(("END")):::startEnd
```

Green circles are the graph's only entry/exit points — one `START`, three possible `END`s (early
refusal, a cache hit, or the normal path). The amber `respond ↔ act` pair is the only cycle in the
whole graph, bounded at two iterations.

## 3.2 Authorization: the five-set intersection (§22.1)

Computed once by the `authorize` node, held constant through the whole `respond ↔ act` loop —
never re-derived mid-turn.

```mermaid
flowchart LR
    classDef input fill:#2f855a,stroke:#1c4532,color:#fff;
    classDef gate fill:#b7791f,stroke:#7b341e,color:#fff,stroke-width:2px;
    classDef result fill:#2b6cb0,stroke:#1a365d,color:#fff,stroke-width:2px;

    A["agent_profile.allowed_tools\nconfig/agents/*.yaml — design-time ceiling"]:::input --> X
    B["HARNESS_AUDIENCE-filtered\ntool manifest\nconfig/tools/*.yaml"]:::input --> X
    C["caller's permissions\n(JWT claim)"]:::input --> X
    D["allow_mutations\n(request option)"]:::input --> X
    E["¬killswitch\n(Redis, 10s cache)"]:::input --> X(("∩")):::gate
    X --> Result["candidate tools offered to the model\n(every candidate logged to\naudit.authz_decisions, allow or deny)"]:::result
```

The five green inputs are where the flow starts; the blue box on the right is where it ends.

A tool surviving this intersection is only offered to the model — `tools/executor.py` re-checks
membership again at *execution* time (§22.4's "pengecekan kedua"), and a `data_scope: self`
violation is rejected outright rather than silently corrected.

## 4. Tool-calling & the two-phase mutation contract (§5.3/§8.4)

Readonly tools (`get_leave_balance`) call `mock-business-api`'s `/query` directly and return in
the same turn. Mutation tools always preview first; only low/medium risk executes immediately.

```mermaid
sequenceDiagram
    participant U as User (via gateway)
    participant H as agent-harness (act node)
    participant B as mock-business-api
    participant Ap as approver (human)

    U->>H: "ajukan cuti 6 hari..."
    H->>H: respond: model calls submit_leave_request
    H->>B: POST .../submit_leave_request/preview
    B-->>H: PreviewResponse (risk_level, preview_token)
    alt risk_level == high (e.g. leave_days > 5)
        H->>H: INSERT audit.mutation_requests\n(status=awaiting_approval)
        H-->>U: 200, pending_approvals=[{approval_id}]
        Note over U,Ap: turn ends here — no execute call yet
        Ap->>H: POST /v1/approvals/{id}/decision (via gateway)
        H->>H: race-safe claim\nUPDATE ... WHERE status='awaiting_approval'
        H->>H: approver permission + no-self-approval check
        H->>B: POST .../execute (stored preview_token + idempotency key)
        B-->>H: ExecuteResponse (business_ref)
        Note over H,B: a replayed decision hits the SAME\nidempotency key → same business_ref,\nnever a second mutation
    else risk_level == low/medium
        H->>B: POST .../execute (immediately, same turn)
        B-->>H: ExecuteResponse
        H-->>U: 200, mutations_executed=[...]
    end
```

## 5. Async job path (§5.9/§5.10)

```mermaid
sequenceDiagram
    participant C as Client
    participant G as agent-gateway
    participant Q as RabbitMQ (agent.jobs exchange)
    participant W as async-worker
    participant H as agent-harness

    C->>G: POST /v1/agent/jobs
    G->>G: reserve ASYNC quota pool (never sync's)
    G->>G: write jobs.async_jobs row (status=queued)
    G->>Q: publish AsyncJobMessage\n(routing key job.standard.* / job.bulk.*)
    G-->>C: 202 {job_id}

    Q->>W: deliver (prefetch 4 standard / 1 bulk)
    W->>W: try_claim_job\nUPDATE ... WHERE status IN (queued,failed)
    W->>H: POST /internal/v1/runs (async-pool LiteLLM key)
    alt success
        H-->>W: AgentInvokeResponse
        W->>W: status=succeeded
        W-->>C: webhook, X-Duta-Signature header (sha256 HMAC)
    else failure, attempts < MAX_ATTEMPTS
        W->>Q: republish to agent.jobs.retry.holding\n(per-message TTL: 10s → 60s → 300s)
        Q-->>Q: TTL expires → dead-lettered back to\nagent.jobs exchange, original routing key
        Q->>W: redelivered
    else failure, attempts exhausted / non-retriable / poison pill
        W->>Q: publish to agent.jobs.dlq directly\n(never an implicit nack-requeue)
        W->>W: status=dead_lettered
        W-->>C: webhook (terminal failure)
    end
    C->>G: GET /v1/agent/jobs/{id} (poll, tenant-scoped)
```

## 6. Ingestion pipeline (§5.11)

```mermaid
flowchart LR
    classDef startEnd fill:#2f855a,stroke:#1c4532,color:#fff,stroke-width:2px;
    classDef step fill:#2b6cb0,stroke:#1a365d,color:#fff;
    classDef store fill:#6b46c1,stroke:#44337a,color:#fff;
    classDef sideEffect fill:#718096,stroke:#2d3748,color:#fff;

    FS(["start:\nseed/documents/*.md"]):::startEnd --> Conn["connectors/filesystem.py\nmanual frontmatter parser"]:::step
    Conn --> Chunk["chunking.py\nheader-aware, tracks H1>H2\nsection_path breadcrumbs"]:::step
    Chunk --> Embed["model-router\nembedding-default → Infinity"]:::step
    Embed --> Upsert["repository.py\ncontent-hash incremental sync +\nsoft-delete tombstoning"]:::step
    Upsert --> PG[("end:\ncatalog.documents / catalog.chunks")]:::store
    Upsert -.->|"changed/tombstoned document_ids"| Inv["side effect:\nPOST harness /internal/v1/cache/invalidate\n(best-effort, logs-and-continues)"]:::sideEffect
```

Each document runs in its own `tenant_session` (§5.11) through this whole chain — one bad
document's error can't abort another document's insert in the same run.

## 7. Retrieval: hybrid search (§28.9)

```mermaid
flowchart LR
    classDef startEnd fill:#2f855a,stroke:#1c4532,color:#fff,stroke-width:2px;
    classDef step fill:#2b6cb0,stroke:#1a365d,color:#fff;
    classDef warn fill:#c53030,stroke:#742a2a,color:#fff;

    Q(["start: query text"]):::startEnd --> Dense["dense candidates\npgvector cosine distance\n(hnsw.iterative_scan=relaxed_order)"]:::step
    Q --> Sparse["sparse candidates\ntsvector / ts_rank_cd"]:::step
    Dense --> RRF["Reciprocal Rank Fusion\nconstant=60"]:::step
    Sparse --> RRF
    RRF --> Rerank["Infinity rerank\n(direct call, no LiteLLM abstraction)"]:::step
    Rerank --> ACL["acl_group_ids && caller's acl\n(tenant_id enforced separately by RLS,\nnot in this WHERE clause)"]:::step
    ACL --> Result(["end: ranked chunks + citations"]):::startEnd
    Rerank -. "rerank failure" .-> Degraded["degraded=['rerank']\n(embedding failure has no fallback\n— fails closed instead)"]:::warn
```

## 8. Semantic cache (§10)

The one field that makes an ACL-gated RAG answer safe to share across users is `acl_hash` in the
namespace key — not a separate check layered on afterward.

```mermaid
flowchart TD
    classDef startNode fill:#2f855a,stroke:#1c4532,color:#fff,stroke-width:2px;
    classDef step fill:#2b6cb0,stroke:#1a365d,color:#fff;
    classDef hitEnd fill:#2f855a,stroke:#1c4532,color:#fff,stroke-width:2px;
    classDef missEnd fill:#718096,stroke:#2d3748,color:#fff;
    classDef decision fill:#b7791f,stroke:#7b341e,color:#fff;

    Query(["start: normalized query +\ntenant_id + agent_id + acl_group_ids"]):::startNode --> Hash["acl_hash = sha256(sorted(acl_group_ids))[:16]"]:::step
    Hash --> NS["namespace:\nsemcache:{tenant_id}:{agent_id}:{acl_hash}:{prompt_version}"]:::step
    NS --> KNN["RediSearch KNN lookup (db 0 only —\nFT.SEARCH doesn't work on other logical DBs)"]:::step
    KNN -- hit --> Skip(["end: cache_hit=true\n0 tokens, 0 cost, graph short-circuits"]):::hitEnd
    KNN -- miss --> Run["run the full graph"]:::step
    Run --> Elig{"eligible to write back?\n(cache/eligibility.py, ALL must hold)"}:::decision
    Elig -->|"agent+tool cacheable:true\nnot refused, retrieval not degraded\nno guardrail flag / PII event"| Write(["end: written to Redis"]):::hitEnd
    Elig -->|"any condition fails —\ne.g. one personal-data tool call"| Skip2(["end: never cached"]):::missEnd
```

## 9. Evaluation pipeline (§13)

`services/eval` is a CLI, never a container — it drives the already-running stack over the same
public surface a real client would use.

```mermaid
sequenceDiagram
    participant Eval as eval-service (uv run)
    participant Idp as mock-idp
    participant K as Kong
    participant H as agent-harness
    participant J as eval-judge (via model-router)
    participant DB as postgres (eval schema)

    Eval->>DB: sync_golden_set (upsert datasets/items, idempotent)
    loop each golden-set item
        Eval->>Idp: POST /oauth/eval-impersonate {user_id}
        Idp->>Idp: 403 unless user.tenant_id ∈ EVAL_TENANT_IDS
        Idp-->>Eval: scoped access_token
        Eval->>K: POST /v1/agent/invoke\nX-Eval-Mode: true
        K->>H: proxy
        H->>H: attach _eval bundle only if\ntenant_id ∈ eval_tenant_id_set\n(harness alone decides, §8.4)
        H-->>Eval: AgentInvokeResponse (+ _eval bundle)
        Eval->>Eval: 6 deterministic metrics (pure functions,\nno LLM — tool_selection, mutation_safety,\ncitation_validity, refusal_appropriateness,\ncapability_leak, pii_leakage)
        Eval->>DB: judge cache lookup (item_id, sha256(response),\njudge_model_version, metric)
        alt cache miss
            Eval->>J: Ragas judged metrics (temperature=0, seed=42)
            J-->>Eval: faithfulness / answer_relevancy /\ncontext_precision / context_recall
            Eval->>DB: cache the judge result
        end
        Note over Eval: a judge failure is caught per-item —\nlogged to judge_errors, never crashes the run
    end
    Eval->>DB: persist eval.runs (raw scores)
    Eval->>Eval: gate.py — median-of-k, fixed-seed bootstrap CI,\nabsolute floor, zero-tolerance metrics never overridable
    Eval->>Eval: report.py — render markdown+JSON under reports/
```

Zero-tolerance deterministic metrics (`mutation_safety`, `capability_leak`, `pii_leakage`) block
the gate on any nonzero count, with no override possible — everything else is a threshold that can
be overridden with two reviewers (§13.8's verdict table). Re-running `gate.py` against an
already-persisted `run_id` never re-invokes the LLM and produces a byte-identical verdict — the
literal §15 DoD, proven by `services/eval/tests/unit/test_gate.py` and live.

**A separate, parallel flow — `make eval-nightly-sample`** (§13.9) reads real production traces
back out of Langfuse's public API (`clients/langfuse.py`, never ClickHouse directly — same
"don't reach into another system's private storage" reasoning as boundary #2's own schema
isolation) instead of the golden set, scores each with a new hand-rolled `citation_support` judge
plus the two Ragas metrics that don't need a ground-truth `reference` (`faithfulness`,
`answer_relevancy` — `context_precision`/`context_recall` need one no production trace has), and
writes the lowest-scoring N to a markdown report for a human to read. It never writes to
`eval.items` or `seed/eval/golden_set.yaml` — §13.9's own text is explicit that curation must stay
manual, or the golden set's quality degrades silently over time.
