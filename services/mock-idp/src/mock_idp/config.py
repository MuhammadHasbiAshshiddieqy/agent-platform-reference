from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    users_path: Path = Path("/app/seed/users.yaml")

    # MUST match gateway's/Kong's JWT_SIGNING_SECRET (§28.10 ADR-009's
    # docstring in services/gateway/src/gateway/auth.py explains the
    # HS256-shared-secret POC simplification vs. real JWKS/RS256).
    jwt_signing_secret: str
    jwt_issuer: str = "duta-demo"

    # 15 min — matches tests/integration/conftest.py's mint_jwt fixture.
    login_token_ttl_seconds: float = 900.0
    # §22.5 — "lifetime = 60 detik", literal spec value.
    exchange_token_ttl_seconds: float = 60.0

    # §13.7 — comma-separated (see harness/config.py's own
    # `eval_tenant_ids` docstring for why not a JSON list).
    eval_tenant_ids: str = "tnt_eval"

    log_level: str = "INFO"

    @property
    def eval_tenant_id_set(self) -> set[str]:
        return {t.strip() for t in self.eval_tenant_ids.split(",") if t.strip()}


settings = Settings()  # type: ignore[call-arg]
