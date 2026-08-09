from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # agent_app, never `agent` — see migrations/versions/0008_app_role.py
    # and CLAUDE.md boundary #4.
    database_url: str

    harness_url: str = "http://agent-harness:8081"
    sync_timeout_seconds: float = 30.0  # §14 gateway->harness timeout

    # HS256 shared secret, dev-only stand-in for mock-idp's JWKS (M5b,
    # §28.10 ADR-009). Kong already validated the JWT at the edge (§7.1);
    # the gateway re-verifies rather than trusting an unverified decode,
    # matching this spec's "no service trusts its caller's word for it"
    # principle (§8.4's identical rule for business-api/harness).
    jwt_signing_secret: str

    # §5.7 keyspace convention — a Redis DB index distinct from harness's
    # (once harness gets its own cache use at M7). §17 reference compose
    # uses redis://redis:6379/1 for agent-gateway specifically.
    redis_url: str = "redis://redis:6379/1"

    # §6 L2 quota — default pool ceilings. Only `sync` is wired at M2;
    # `async` is defined now so the keyspace/limits exist before M6's
    # worker needs them, but nothing reserves against it yet.
    sync_tokens_per_hour: int = 500_000
    async_tokens_per_day: int = 5_000_000
    # §23.2a — reservation TTL = max run timeout + 60s grace period, so
    # the sweeper gets a window to reclaim it before Redis's own passive
    # expiry silently deletes the evidence.
    quota_sweep_interval_seconds: float = 30.0

    # §5.9 — gateway only ever publishes here; async-worker owns the
    # actual queue/DLX/retry topology declaration.
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"
    # §23.2a's reservation-TTL reasoning applied to the async pool: a job
    # can sit queued behind others before a worker even starts it, so this
    # needs to be far more generous than sync's per-request timeout, or
    # the sweeper reclaims the reservation before the worker ever gets to
    # reconcile it (§23.2a's own documented tradeoff — noted, not new to M6).
    async_job_deadline_seconds: float = 600.0

    log_level: str = "INFO"
    log_format: str = "json"


settings = Settings()  # type: ignore[call-arg]
