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
