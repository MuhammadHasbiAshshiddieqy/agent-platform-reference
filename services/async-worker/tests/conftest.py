"""`config.Settings` requires `DATABASE_URL`/`MODEL_ROUTER_MASTER_KEY`
with no defaults — set before `async_worker.processor` (which imports
`async_worker.persistence.db`, which imports `async_worker.config` at
module level) is ever collected. Same pattern as `services/mock-business-
api/tests/conftest.py`. `test_processor.py` never actually opens the DB
connection this DSN implies (its persistence calls are monkeypatched),
so any syntactically-valid value works.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("MODEL_ROUTER_MASTER_KEY", "test-master-key")
