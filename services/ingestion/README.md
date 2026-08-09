# ingestion-service

RAG write side (§5.11). M3 scope: filesystem connector reading
`seed/documents/*.md` (YAML frontmatter for `tenant_id`/`acl_group_ids`),
header-aware chunking, embedding via `model-router`, upsert into
`catalog.documents` / `catalog.chunks`, incremental sync via
`content_hash`, tombstone for removed documents. One document's failure
doesn't stop the run (`catalog.ingestion_errors`).

## Run standalone

```bash
uv run --package ingestion uvicorn ingestion.main:app --reload --port 8083
curl -X POST http://localhost:8083/internal/v1/ingest/tnt_demo
```

Required env vars: see `ingestion.config.Settings` — `DATABASE_URL`
(`agent_app`), `MODEL_ROUTER_URL`, `MODEL_ROUTER_KEY`, `DOCUMENTS_DIR`.

## Test

```bash
uv run pytest services/ingestion/tests
```
