"""`config.Settings` requires `DATABASE_URL`/`MODEL_ROUTER_KEY` with no
defaults — set before any `eval_service.*` module (which import
`eval_service.config` transitively) is collected. Same pattern as
`services/async-worker/tests/conftest.py`. None of `tests/unit/` opens a
real DB connection or calls model-router, so any syntactically-valid
value works.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("MODEL_ROUTER_KEY", "test-model-router-key")
