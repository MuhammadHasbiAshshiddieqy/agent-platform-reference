"""M1 integration tests run against the live `docker compose` stack (`make
up`), not testcontainers — the whole point is proving Kong -> gateway ->
harness -> model-router works together, which testcontainers can't stand
up on its own (Kong's declarative config, LiteLLM, Ollama...).

Every fixture here reads the same `.env` the compose stack was started
with, so credentials always match what's actually running.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import jwt
import psycopg
import pytest
import redis

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key] = value
    return values


@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    return _load_env_file(REPO_ROOT / ".env")


@pytest.fixture(scope="session")
def kong_url() -> str:
    return "http://localhost:8000"


@pytest.fixture(scope="session")
def langfuse_url() -> str:
    return "http://localhost:3000"


@pytest.fixture()
def db_conn(env: dict[str, str]) -> Iterator[psycopg.Connection]:
    # agent_app, not agent — proves the app-facing DSN actually works end
    # to end, not just the migration superuser (§23.2k, CLAUDE.md #4).
    dsn = f"postgresql://agent_app:{env['APP_DB_PASSWORD']}@localhost:5432/agent_platform"
    with psycopg.connect(dsn) as conn:
        yield conn


@pytest.fixture()
def gateway_redis() -> Iterator[redis.Redis]:
    # db 1 — matches agent-gateway's REDIS_URL in deploy/docker-compose.yml
    # (§5.7 keyspace convention). Same instance the live gateway reserves
    # quota against, not a separate one — the M2 tests seed/inspect this
    # directly rather than waiting on real token volume to hit a limit.
    client = redis.Redis.from_url("redis://localhost:6379/1")
    yield client
    client.close()


@pytest.fixture(autouse=True, scope="session")
def _require_stack_env() -> None:
    if os.environ.get("SKIP_M1_INTEGRATION"):
        pytest.skip("SKIP_M1_INTEGRATION set")


@pytest.fixture()
def mint_jwt(env: dict[str, str]) -> Callable[..., str]:
    """Raw HS256 JWT, not mock-idp (M5b) — Kong's `jwt` plugin looks up a
    consumer by the `iss` claim (config/kong/kong.yml), not by username,
    so every token needs `iss=duta-demo` to route at all.
    """

    def _mint(
        *,
        tenant_id: str = "tnt_demo",
        user_id: str = "usr_budi",
        acl_group_ids: list[str] | None = None,
        # §22.3's claim shape (see contracts/harness.py's AgentRunRequest
        # docstring, M5) — no default derivation from user_id here, unlike
        # mock-idp's eventual real lookup (M5b): tests pass what their
        # scenario needs explicitly, kept obvious to read at the call site.
        employee_id: str | None = None,
        permissions: list[str] | None = None,
        # §22.3's remaining claims (M5b) — same "tests pass what their
        # scenario needs explicitly" reasoning as employee_id/permissions
        # above; mock-idp (services/mock-idp) mints the real thing from
        # seed/users.yaml, exercised directly by its own contract tests
        # and by tests/integration/test_m5b_rbac.py's token-exchange case,
        # not by every test in this file re-routing through an HTTP call.
        roles: list[str] | None = None,
        scope_context: dict[str, object] | None = None,
    ) -> str:
        now = int(time.time())
        payload = {
            "iss": "duta-demo",
            "sub": user_id,
            "tenant_id": tenant_id,
            "acl_group_ids": acl_group_ids or [],
            "employee_id": employee_id,
            "permissions": permissions or [],
            "roles": roles or [],
            "scope_context": scope_context or {},
            "iat": now,
            # 15 minutes, not 5 — this dev machine's full-suite runs
            # occasionally back up badly enough under CPU contention
            # (every test does a real local LLM call) that a tighter
            # window risked the token expiring before Kong/gateway ever
            # got scheduled to look at it. See CLAUDE.md's environment
            # quirks section.
            "exp": now + 900,
        }
        return jwt.encode(payload, env["JWT_SIGNING_SECRET"], algorithm="HS256")

    return _mint
