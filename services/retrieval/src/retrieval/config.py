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

    # Infinity called directly for reranking only — LiteLLM/model-router
    # has no "rerank" endpoint abstraction (§28.3), unlike chat/embeddings.
    # §17's reference compose gives retrieval-service both INFINITY_URL and
    # MODEL_ROUTER_URL for exactly this split.
    infinity_url: str = "http://infinity:7997"
    rerank_model: str = "BAAI/bge-reranker-base"  # see deploy/docker-compose.yml's note on -v2-m3
    rerank_top_n: int = 20

    # §28.9 hybrid query defaults.
    dense_candidates: int = 50
    sparse_candidates: int = 50
    fused_top_n: int = 20
    final_top_k: int = 8

    log_level: str = "INFO"
    log_format: str = "json"


settings = Settings()  # type: ignore[call-arg]
