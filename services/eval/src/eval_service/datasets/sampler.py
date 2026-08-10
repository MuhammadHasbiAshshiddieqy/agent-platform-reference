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
    """Picks up to `per_tag_quota` items per tag, stable-ordered by `id`
    within each tag so the result is identical across runs given the same
    input set, then tops up from the remainder up to `total_size`.

    `per_tag_quota` is a floor, not a best-effort target — the whole point
    of stratifying is the guarantee "Sampling acak murni akan sesekali
    menghasilkan batch tanpa satu pun kasus RBAC" is never true. Two
    things are required to actually keep that guarantee, both of which a
    naive slice-then-truncate implementation gets wrong:

    1. An item can carry more than one tag (`tags: [rbac, mutation]` is
       real data in `seed/eval/golden_set.yaml`). If tag A is processed
       first (tags are walked in sorted order) and claims a shared item,
       tag B must keep walking *its own* bucket past that item to find a
       genuinely new one — stopping at a fixed `tagged[:per_tag_quota]`
       slice instead silently costs tag B one of its guaranteed slots
       whenever a substitute was sitting just past the cutoff.
    2. `total_size` must never truncate a tag's already-guaranteed quota
       items. A blind `selected[:total_size]` slice operates on a
       tag-grouped list (tags earlier in sort order fill in first), so it
       can drop a later tag's entire quota even though every item in
       `selected` up to that point came from a legitimate quota claim.
       `total_size` is therefore only a cap on how much the *remainder*
       phase tops up — a caller whose `per_tag_quota * len(tags)` exceeds
       `total_size` gets a sample somewhat larger than `total_size` rather
       than a broken stratification guarantee (§13.6's size target is a
       soft time/cost budget; the quota is a correctness requirement).
    """
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        for tag in item.get("tags", []):
            by_tag.setdefault(tag, []).append(item)

    selected_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    for tag in sorted(by_tag):
        tagged = sorted(by_tag[tag], key=lambda i: i["id"])
        added_for_tag = 0
        for item in tagged:
            if added_for_tag >= per_tag_quota:
                break
            if item["id"] not in selected_ids:
                selected_ids.add(item["id"])
                selected.append(item)
                added_for_tag += 1

    remainder = sorted((i for i in items if i["id"] not in selected_ids), key=lambda i: i["id"])
    for item in remainder:
        if len(selected) >= total_size:
            break
        selected_ids.add(item["id"])
        selected.append(item)

    return sorted(selected, key=lambda i: i["id"])
