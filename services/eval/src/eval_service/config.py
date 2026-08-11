from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # agent_app, never `agent` — CLAUDE.md boundary #4, same as every
    # other service. `eval` has no RLS (migration 0006's own docstring:
    # eval datasets are org-wide golden sets, not tenant data) but still
    # uses the app role, never the migration superuser.
    database_url: str

    # §13.7 — eval-service calls the PUBLIC gateway, never harness
    # directly ("jalur yang dievaluasi harus jalur yang sama dengan yang
    # dipakai pengguna").
    gateway_url: str = "http://localhost:8000"
    gateway_timeout_seconds: float = 180.0

    mock_idp_url: str = "http://localhost:8087"
    mock_idp_timeout_seconds: float = 10.0

    model_router_url: str = "http://localhost:4000"
    model_router_key: str

    # §13.1 — the only tenant whose users `/oauth/eval-impersonate` will
    # mint tokens for; must match mock-idp's own `EVAL_TENANT_IDS`.
    eval_tenant_id: str = "tnt_eval"

    # §13.7 — "Concurrency 8 dipilih agar eval tidak menghabiskan quota
    # tenant eval dan tidak memicu rate limit provider."
    concurrency: int = 8
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0

    # §13.6 — per-tag minimum for the smoke tier's stratified sample.
    # Spec's literal "minimal 8 item" assumes a 100-300 item production
    # golden set; this reference implementation's dataset is 16 items
    # total (seed/eval/golden_set.yaml's own docstring explains the
    # scale-down), so the sampler falls back to "every item with this
    # tag" whenever fewer than the quota exist, documented in
    # `datasets/sampler.py`.
    smoke_per_tag_quota: int = 8
    smoke_sample_size: int = 40

    golden_set_path: Path = (
        Path(__file__).resolve().parents[4] / "seed" / "eval" / "golden_set.yaml"
    )

    # §13.9 — nightly-tier production sampling reads traces back out of
    # the same Langfuse project `services/harness` writes to. Field names
    # and defaults deliberately match `harness.config.Settings` — same
    # project, same credentials, run from localhost instead of in-cluster.
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    reports_dir: Path = Path("reports")

    log_level: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
