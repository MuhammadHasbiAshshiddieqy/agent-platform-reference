"""§13.2 — `seed/eval/golden_set.yaml` (version-controlled) is the source
of truth; this module makes `eval.datasets`/`eval.items` match it. Called
at the start of every `run` invocation — idempotent (`ON CONFLICT DO
UPDATE`), so re-running against an unchanged file is a no-op in effect,
same "upsert, not insert-or-fail" pattern `ingestion`'s own repository
uses for `catalog.documents`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncConnection

from eval_service.persistence.repository import upsert_dataset, upsert_item


def load_golden_set_file(path: Path) -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    return raw


async def sync_golden_set(conn: AsyncConnection, path: Path) -> tuple[str, str, int]:
    """Returns (dataset_id, agent_id, item_count)."""
    raw = load_golden_set_file(path)
    dataset = raw["dataset"]
    await upsert_dataset(
        conn,
        dataset_id=dataset["id"],
        name=dataset["name"],
        agent_id=dataset["agent_id"],
        description=dataset.get("description"),
    )
    items = raw["items"]
    for item in items:
        await upsert_item(conn, item={**item, "dataset_id": dataset["id"]})
    return dataset["id"], dataset["agent_id"], len(items)
