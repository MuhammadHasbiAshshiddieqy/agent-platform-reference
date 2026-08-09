"""In-memory mutable business state. A real business-api would back this
with its own database — §5.6's schema table deliberately assigns no
Postgres schema to mock-business-api, since only the *contract* (§8.4)
and its enforcement (conformance suite, §24.1) are this repo's concern,
not payroll/leave persistence logic (§5.12: "dimiliki tim domain, bukan
tim platform AI"). In-memory is sanctioned by §24 itself, not a shortcut.

Seeded once at startup from `seed/business_state.json` (mutable state:
leave balances, request history) and `seed/users.yaml` (read-only here —
`auth.py` uses it for the actor-permission check §8.4 requires business-
api to make independently of harness). Both reload on `reset()`, which
`POST /internal/v1/reset` calls for `make reset` (§27.2) — a plain
in-process reset, not a container restart.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class BusinessState:
    def __init__(self, business_state_path: Path, users_path: Path) -> None:
        self._business_state_path = business_state_path
        self._users_path = users_path
        self._lock = asyncio.Lock()
        self.leave_balances: dict[str, int] = {}
        self.leave_requests: list[dict[str, Any]] = []
        self.users: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        import yaml

        data = json.loads(self._business_state_path.read_text())
        self.leave_balances = dict(data["leave_balances"])
        self.leave_requests = list(data["leave_requests"])

        users_data = yaml.safe_load(self._users_path.read_text())
        self.users = {u["user_id"]: u for u in users_data["users"]}

    def reset(self) -> None:
        self.leave_requests = []
        self._load()

    async def deduct_leave(self, employee_id: str, days: int) -> int:
        async with self._lock:
            balance = self.leave_balances.get(employee_id, 0)
            new_balance = balance - days
            self.leave_balances[employee_id] = new_balance
            return new_balance

    async def record_leave_request(self, record: dict[str, Any]) -> None:
        async with self._lock:
            self.leave_requests.append(record)
