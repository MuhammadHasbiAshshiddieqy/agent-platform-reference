from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # agent_app, never `agent` — CLAUDE.md boundary #4.
    database_url: str

    harness_url: str = "http://agent-harness:8081"
    # §5.10 — async gets more slack than sync's per-request timeout: it's
    # explicitly the "not in a hurry" pool (§6), and a bulk summarization
    # prompt is naturally slower than a one-line chat answer.
    harness_timeout_seconds: float = 300.0

    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"
    # §5.9 — "prefetch rendah" for bulk specifically, so one slow bulk job
    # doesn't let a worker instance grab a pile of others behind it while
    # standard-priority jobs are waiting on a different queue entirely.
    standard_prefetch: int = 4
    bulk_prefetch: int = 1

    # §6 L2 async-pool reconciliation — same Redis instance/db gateway's
    # QuotaManager reserves against. A separate copy of the reconcile Lua
    # script, not an import of gateway's QuotaManager (§4.1).
    quota_redis_url: str = "redis://redis:6379/1"

    # §5.10 — worker's own LiteLLM virtual key, provisioned at startup
    # (litellm_key.py), distinct budget from the sync path's key.
    model_router_url: str = "http://model-router:4000"
    model_router_master_key: str
    async_pool_daily_budget_usd: float = 5.0
    async_pool_key_alias: str = "async-pool"
    async_pool_key_path: Path = Path("/app/data/async_pool_key.json")

    webhook_secrets_path: Path = Path("/app/seed/webhook_secrets.yaml")
    webhook_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 5

    log_level: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
