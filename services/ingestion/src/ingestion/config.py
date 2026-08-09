from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # agent_app, never `agent` — see migrations/versions/0008_app_role.py
    # and CLAUDE.md boundary #4.
    database_url: str

    model_router_url: str = "http://model-router:4000"
    model_router_key: str
    embedding_model_alias: str = "embedding-default"  # §5.4 — never a provider model id here
    embedding_dim: int = 1024  # bge-m3 (§28.4) — pinned; a new model needs a new vector column

    documents_dir: str = "/seed/documents"

    # §10's cache-invalidation event — a direct service-to-service call
    # (no Kong route, same "internal/operational" class as retrieval's
    # own /internal/v1/search), best-effort from this side (see
    # clients/harness.py's docstring).
    harness_url: str = "http://agent-harness:8081"
    harness_timeout_seconds: float = 10.0

    # §11.1 chunking targets, in characters — no tokenizer dependency, same
    # ~4 chars/token heuristic as gateway's quota estimate (services/gateway/
    # src/gateway/quota.py). 512 tokens / 64 overlap -> ~2048 / ~256 chars.
    chunk_target_chars: int = 2048
    chunk_overlap_chars: int = 256

    log_level: str = "INFO"
    log_format: str = "json"


settings = Settings()  # type: ignore[call-arg]
