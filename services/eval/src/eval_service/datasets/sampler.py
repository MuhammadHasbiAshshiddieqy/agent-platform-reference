"""§13.6 — smoke tier's stratified sample: per-tag minimum quota, not
pure random selection ("Sampling acak murni akan sesekali menghasilkan
batch tanpa satu pun kasus RBAC"). Deterministic (no `random` module) —
the selection itself must not be a source of run-to-run flakiness on top
of the model's own.
"""

from __future__ import annotations

from typing import Any


def stratified_sample(
    items: list[dict[str, Any]], *, per_tag_quota: int, total_size: int
) -> list[dict[str, Any]]:
    """Picks up to `per_tag_quota` items per tag (all of them if fewer
    exist — see `config.py`'s `smoke_per_tag_quota` docstring for why
    this reference implementation's 16-item dataset routinely hits that
    fallback), stable-ordered by `id` within each tag so the result is
    identical across runs given the same input set, then truncates (or
    tops up from the remainder) to `total_size`.
    """
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        for tag in item.get("tags", []):
            by_tag.setdefault(tag, []).append(item)

    selected_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    for tag in sorted(by_tag):
        tagged = sorted(by_tag[tag], key=lambda i: i["id"])
        for item in tagged[:per_tag_quota]:
            if item["id"] not in selected_ids:
                selected_ids.add(item["id"])
                selected.append(item)

    if len(selected) < total_size:
        remainder = sorted((i for i in items if i["id"] not in selected_ids), key=lambda i: i["id"])
        for item in remainder:
            if len(selected) >= total_size:
                break
            selected_ids.add(item["id"])
            selected.append(item)

    return sorted(selected[:total_size], key=lambda i: i["id"])
