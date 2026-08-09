from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    business_state_path: Path = Path("/app/seed/business_state.json")
    users_path: Path = Path("/app/seed/users.yaml")

    # §8.4 — preview_token TTL 5 minutes.
    preview_token_ttl_seconds: float = 300.0

    # §22.5 step 5 — "Business-api memvalidasi token secara independen ke
    # IdP": same shared secret mock-idp signs with, verified locally here
    # rather than a network round-trip back to mock-idp per call. MUST
    # match mock-idp's/gateway's/Kong's JWT_SIGNING_SECRET.
    jwt_signing_secret: str
    jwt_issuer: str = "duta-demo"

    log_level: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
