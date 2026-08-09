"""`config.Settings` defaults `users_path` to the container path
(`/app/seed/users.yaml`) and requires `JWT_SIGNING_SECRET` with no
default — both need setting before `mock_idp.main.app` is ever imported
(pydantic-settings reads the environment at class instantiation time,
`config.py`'s module-level `settings = Settings()`). Same pattern as
`services/mock-business-api/tests/conftest.py` and
`services/retrieval/tests/*/conftest.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

os.environ.setdefault("USERS_PATH", str(REPO_ROOT / "seed" / "users.yaml"))
os.environ.setdefault("JWT_SIGNING_SECRET", "duta-dev-jwt-signing-secret-never-use-in-production")
