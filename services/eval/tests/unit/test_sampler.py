"""§13.6's stratified sampler — deterministic (no `random`), per-tag
quota with graceful fallback when a tag has fewer items than the quota.
"""

from __future__ import annotations

from eval_service.datasets.sampler import stratified_sample


def _items(specs: list[tuple[str, list[str]]]) -> list[dict[str, object]]:
    return [{"id": item_id, "tags": tags} for item_id, tags in specs]


def test_every_tag_gets_represented_even_with_a_small_quota() -> None:
    items = _items(
        [
            ("it_1", ["rag"]),
            ("it_2", ["rag"]),
            ("it_3", ["rbac"]),
            ("it_4", ["mutation"]),
            ("it_5", ["refusal"]),
        ]
    )
    sample = stratified_sample(items, per_tag_quota=1, total_size=10)
    tags_present = {tag for item in sample for tag in item["tags"]}  # type: ignore[misc]
    assert tags_present == {"rag", "rbac", "mutation", "refusal"}


def test_falls_back_to_all_items_when_fewer_than_quota_exist() -> None:
    items = _items([("it_1", ["rag"]), ("it_2", ["rag"])])
    sample = stratified_sample(items, per_tag_quota=8, total_size=40)
    assert {item["id"] for item in sample} == {"it_1", "it_2"}


def test_sampling_is_deterministic_across_calls() -> None:
    items = _items([(f"it_{i}", ["rag" if i % 2 == 0 else "rbac"]) for i in range(20)])
    sample_1 = [item["id"] for item in stratified_sample(items, per_tag_quota=3, total_size=10)]
    sample_2 = [item["id"] for item in stratified_sample(items, per_tag_quota=3, total_size=10)]
    assert sample_1 == sample_2


def test_total_size_is_respected() -> None:
    items = _items([(f"it_{i}", ["rag"]) for i in range(50)])
    sample = stratified_sample(items, per_tag_quota=8, total_size=10)
    assert len(sample) == 10


def test_a_tag_still_reaches_its_quota_when_a_shared_item_is_claimed_by_an_earlier_tag() -> None:
    # it_a is claimed by "mutation" (processed first, alphabetically) before "rbac"
    # gets to it. rbac's own bucket has two other real candidates (it_c, it_d) — a
    # correct implementation walks past the already-claimed it_a to find both of
    # them rather than silently costing rbac one of its two guaranteed slots.
    # total_size=3 is deliberately tight so the remainder phase can't mask the bug
    # by backfilling it_d anyway.
    items = _items(
        [
            ("it_a", ["mutation", "rbac"]),
            ("it_b", ["mutation"]),
            ("it_c", ["rbac"]),
            ("it_d", ["rbac"]),
        ]
    )
    sample = stratified_sample(items, per_tag_quota=2, total_size=3)
    rbac_ids = {item["id"] for item in sample if "rbac" in item["tags"]}  # type: ignore[operator]
    assert {"it_c", "it_d"}.issubset(rbac_ids)


def test_total_size_never_truncates_a_tags_already_guaranteed_quota() -> None:
    # per_tag_quota=3 * 2 tags = 6 potential quota items, but total_size caps at 4.
    # Every tag must still keep its full quota — total_size is a soft budget for the
    # remainder top-up only, never grounds to break the stratification guarantee.
    items = _items([(f"it_mut_{i}", ["mutation"]) for i in range(3)]) + _items(
        [(f"it_rbac_{i}", ["rbac"]) for i in range(3)]
    )
    sample = stratified_sample(items, per_tag_quota=3, total_size=4)
    mutation_count = sum(1 for item in sample if "mutation" in item["tags"])  # type: ignore[operator]
    rbac_count = sum(1 for item in sample if "rbac" in item["tags"])  # type: ignore[operator]
    assert mutation_count == 3
    assert rbac_count == 3
