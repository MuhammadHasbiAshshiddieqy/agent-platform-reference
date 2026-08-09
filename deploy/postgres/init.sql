-- Runs exactly once: only when the postgres container starts against an
-- empty data directory (docker-entrypoint-initdb.d semantics). Creates the
-- two additional logical databases the single shared instance hosts
-- alongside POSTGRES_DB=agent_platform (§17, §28.8) — plus the litellm and
-- langfuse databases now, even though model-router (M1) and Langfuse's own
-- app code don't run yet, because this script will NOT run again once the
-- volume exists. Adding a database later means a manual migration, not a
-- docker compose up.

CREATE DATABASE litellm;
CREATE DATABASE langfuse;

-- pgvector is the vector store (ADR-002, §28.4) and lives only in
-- agent_platform, alongside catalog.chunks.
\c agent_platform
CREATE EXTENSION IF NOT EXISTS vector;
