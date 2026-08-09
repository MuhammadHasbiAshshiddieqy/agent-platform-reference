"""Read-only user directory, loaded from `seed/users.yaml` — the *same*
file `mock-business-api` reads (ADR-009: one source of truth, never a
copy). mock-idp never mutates it; there's no `POST /internal/v1/reset`
here because there's no mutable state to reset.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class UserDirectory:
    def __init__(self, users_path: Path) -> None:
        self._users_path = users_path
        self.users_by_id: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        data = yaml.safe_load(self._users_path.read_text())
        self.users_by_id = {u["user_id"]: u for u in data["users"]}

    def get(self, user_id: str) -> dict[str, Any] | None:
        return self.users_by_id.get(user_id)
