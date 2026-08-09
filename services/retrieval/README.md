# retrieval-service

RAG read side (§5.5). Hybrid search (dense `pgvector` + sparse `tsvector`,
fused with RRF — §28.9), reranked via Infinity, tenant/ACL filtered
in-query (RLS + `acl_group_ids && :acl`). Never calls an LLM to answer —
only ever returns chunks with citation metadata.

## Run standalone

```bash
uv run --package retrieval uvicorn retrieval.main:app --reload --port 8082
```

Required env vars: see `retrieval.config.Settings` — `DATABASE_URL`
(`agent_app`), `MODEL_ROUTER_URL`, `MODEL_ROUTER_KEY`, `INFINITY_URL`.

## Test

```bash
uv run pytest services/retrieval/tests
```
