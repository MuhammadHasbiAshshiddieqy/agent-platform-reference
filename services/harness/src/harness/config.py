from __future__ import annotations

from pathlib import Path

from contracts.common import Audience
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # agent_app, never `agent` — see migrations/versions/0008_app_role.py
    # and CLAUDE.md boundary #4. Superusers bypass RLS unconditionally.
    database_url: str

    model_router_url: str = "http://model-router:4000"
    model_router_key: str
    # M5's tool-calling loop can chain several of these calls (up to
    # MAX_TOOL_ITERATIONS respond<->act round-trips) in one request on top
    # of §9's guardrail classifiers — 90s, not the original 60s, gives a
    # single slow call under CPU contention room without blowing through
    # SYNC_TIMEOUT_SECONDS on its own.
    model_router_timeout_seconds: float = 90.0

    retrieval_url: str = "http://retrieval-service:8082"
    retrieval_timeout_seconds: float = 5.0  # §14 harness->retrieval timeout

    business_api_url: str = "http://mock-business-api:8084"
    business_api_timeout_seconds: float = 10.0  # §14 harness->business-api timeout

    mock_idp_url: str = "http://mock-idp:8087"
    mock_idp_timeout_seconds: float = 10.0

    # §21.2 — ADR-011: build the split now, deploy internal-only in POC.
    # A `harness-external` instance sets this to "external" and points
    # `tools_dir`/`agents_dir` at the same `config/` directory — the
    # audience filter does the rest (`authz/manifest.py`'s
    # `load_tool_manifests`, which fails to boot if that filter leaves it
    # with zero tools, §21/ADR-011's own required assertion).
    harness_audience: Audience = Audience.INTERNAL
    tools_dir: Path = Path("/app/config/tools")
    agents_dir: Path = Path("/app/config/agents")

    # §5.7 keyspace convention — a Redis DB index distinct from gateway's
    # (db 1, quota) and langfuse's. db 2 for harness's own use: §22.6's
    # killswitch reads (`killswitch:tool:*`, `killswitch:agent:*`).
    redis_url: str = "redis://redis:6379/2"

    # §10/§5.7 semantic cache — a SEPARATE connection from `redis_url`
    # above, deliberately pinned to db 0: RediSearch's FT.CREATE/FT.SEARCH
    # only operate on logical DB 0 (`cache/store.py`'s docstring has the
    # confirmed error text), not a config preference.
    semantic_cache_redis_url: str = "redis://redis:6379/0"
    semantic_cache_similarity_threshold: float = 0.95
    # bge-m3, matching catalog.chunks' vector(1024) column (§5.8) — the
    # cache and RAG retrieval share one embedding model, never mixed.
    semantic_cache_vector_dim: int = 1024
    embedding_model_alias: str = "embedding-default"

    langfuse_host: str = "http://langfuse:3000"
    langfuse_public_key: str
    langfuse_secret_key: str

    # §13.1 — comma-separated, not a JSON list: every other multi-value
    # setting in this codebase is a plain env var, and pydantic-settings'
    # default JSON parsing for `list[str]` fields would force docker-
    # compose.yml's env value into `["tnt_eval"]` syntax for no benefit.
    eval_tenant_ids: str = "tnt_eval"

    log_level: str = "INFO"
    log_format: str = "json"

    @property
    def eval_tenant_id_set(self) -> set[str]:
        return {t.strip() for t in self.eval_tenant_ids.split(",") if t.strip()}

    @property
    def database_url_psycopg(self) -> str:
        """langgraph-checkpoint-postgres uses psycopg (v3), not asyncpg —
        it needs a plain `postgresql://` DSN, not SQLAlchemy's
        `postgresql+asyncpg://`. Same database, same credentials, two
        drivers with two URL schemes."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


settings = Settings()  # type: ignore[call-arg]
