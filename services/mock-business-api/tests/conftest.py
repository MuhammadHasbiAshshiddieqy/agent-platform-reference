"""`config.Settings` defaults to the container paths (`/app/seed/...`) baked
into the Docker image — correct at runtime, but `test_contract.py` imports
`mock_business_api.main.app` directly (no container), so `Settings()` needs
pointing at the repo's real `seed/` directory instead. Must run before that
import, since pydantic-settings reads the environment at class instantiation
time (`config.py`'s module-level `settings = Settings()`), same pattern as
`services/retrieval/tests/*/conftest.py` setting `DATABASE_URL` before its
service config module is ever imported.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

os.environ.setdefault("BUSINESS_STATE_PATH", str(REPO_ROOT / "seed" / "business_state.json"))
os.environ.setdefault("USERS_PATH", str(REPO_ROOT / "seed" / "users.yaml"))
os.environ.setdefault("JWT_SIGNING_SECRET", "duta-dev-jwt-signing-secret-never-use-in-production")
