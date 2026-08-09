"""§6 L2 quota + §23.2a reservation-leak hazard, tested against a real
Redis (testcontainers) — the point is proving the Lua scripts' atomicity
and the reserve/reconcile/sweep state machine, which a mock can't verify.
"""

from __future__ import annotations

import asyncio

import pytest
from gateway.quota import QuotaManager, estimate_tokens
from redis.asyncio import Redis


@pytest.fixture()
def quota(redis_client: Redis) -> QuotaManager:
    return QuotaManager(redis_client, sync_limit=1000, async_limit=10_000)


class TestEstimateTokens:
    def test_scales_with_input_length_plus_max_output(self) -> None:
        short = estimate_tokens("hi", max_output_tokens=100)
        long = estimate_tokens("hi " * 100, max_output_tokens=100)
        assert short < long

    def test_never_zero_even_for_empty_input(self) -> None:
        assert estimate_tokens("", max_output_tokens=0) >= 1


class TestReserve:
    @pytest.mark.asyncio
    async def test_accepts_reservation_within_limit(self, quota: QuotaManager) -> None:
        result = await quota.reserve(
            tenant_id="tnt_a",
            pool="sync",
            run_id="run_1",
            estimated_tokens=100,
            deadline_seconds=30,
        )
        assert result.accepted
        assert result.reservation_key is not None
        assert result.tokens_after == 100
        assert result.limit == 1000

    @pytest.mark.asyncio
    async def test_rejects_reservation_over_limit(self, quota: QuotaManager) -> None:
        await quota.reserve(
            tenant_id="tnt_b",
            pool="sync",
            run_id="run_1",
            estimated_tokens=950,
            deadline_seconds=30,
        )
        result = await quota.reserve(
            tenant_id="tnt_b",
            pool="sync",
            run_id="run_2",
            estimated_tokens=100,
            deadline_seconds=30,
        )
        assert not result.accepted
        assert result.reservation_key is None
        assert result.retry_after_seconds > 0

    @pytest.mark.asyncio
    async def test_rejected_reservation_does_not_touch_the_bucket(
        self, quota: QuotaManager, redis_client: Redis
    ) -> None:
        await quota.reserve(
            tenant_id="tnt_c",
            pool="sync",
            run_id="run_1",
            estimated_tokens=950,
            deadline_seconds=30,
        )
        before = await redis_client.get("quota:tnt_c:sync")
        await quota.reserve(
            tenant_id="tnt_c",
            pool="sync",
            run_id="run_2",
            estimated_tokens=100,
            deadline_seconds=30,
        )
        after = await redis_client.get("quota:tnt_c:sync")
        assert before == after == b"950"

    @pytest.mark.asyncio
    async def test_two_tenants_are_isolated(self, quota: QuotaManager) -> None:
        # tnt_busy exhausts its own bucket; tnt_quiet must be unaffected —
        # the DoD's "tenant lain tidak terpengaruh" (§6 M2 DoD).
        await quota.reserve(
            tenant_id="tnt_busy",
            pool="sync",
            run_id="r1",
            estimated_tokens=1000,
            deadline_seconds=30,
        )
        busy_rejected = await quota.reserve(
            tenant_id="tnt_busy", pool="sync", run_id="r2", estimated_tokens=1, deadline_seconds=30
        )
        quiet_accepted = await quota.reserve(
            tenant_id="tnt_quiet",
            pool="sync",
            run_id="r1",
            estimated_tokens=1000,
            deadline_seconds=30,
        )
        assert not busy_rejected.accepted
        assert quiet_accepted.accepted


class TestReconcile:
    @pytest.mark.asyncio
    async def test_returns_unused_tokens_to_the_bucket(
        self, quota: QuotaManager, redis_client: Redis
    ) -> None:
        result = await quota.reserve(
            tenant_id="tnt_d",
            pool="sync",
            run_id="run_1",
            estimated_tokens=100,
            deadline_seconds=30,
        )
        assert result.reservation_key is not None
        await quota.reconcile(result.reservation_key, actual_tokens=30)
        assert await redis_client.get("quota:tnt_d:sync") == b"30"

    @pytest.mark.asyncio
    async def test_takes_additional_tokens_when_actual_exceeds_estimate(
        self, quota: QuotaManager, redis_client: Redis
    ) -> None:
        result = await quota.reserve(
            tenant_id="tnt_e",
            pool="sync",
            run_id="run_1",
            estimated_tokens=50,
            deadline_seconds=30,
        )
        assert result.reservation_key is not None
        await quota.reconcile(result.reservation_key, actual_tokens=80)
        assert await redis_client.get("quota:tnt_e:sync") == b"80"

    @pytest.mark.asyncio
    async def test_deletes_the_reservation_key(
        self, quota: QuotaManager, redis_client: Redis
    ) -> None:
        result = await quota.reserve(
            tenant_id="tnt_f",
            pool="sync",
            run_id="run_1",
            estimated_tokens=50,
            deadline_seconds=30,
        )
        assert result.reservation_key is not None
        await quota.reconcile(result.reservation_key, actual_tokens=50)
        assert await redis_client.get(result.reservation_key) is None

    @pytest.mark.asyncio
    async def test_is_idempotent_on_a_second_call(
        self, quota: QuotaManager, redis_client: Redis
    ) -> None:
        result = await quota.reserve(
            tenant_id="tnt_g",
            pool="sync",
            run_id="run_1",
            estimated_tokens=100,
            deadline_seconds=30,
        )
        assert result.reservation_key is not None
        await quota.reconcile(result.reservation_key, actual_tokens=40)
        await quota.reconcile(result.reservation_key, actual_tokens=999)  # must be a no-op
        assert await redis_client.get("quota:tnt_g:sync") == b"40"


class TestSweeper:
    @pytest.mark.asyncio
    async def test_reclaims_a_reservation_past_its_deadline(
        self, quota: QuotaManager, redis_client: Redis
    ) -> None:
        result = await quota.reserve(
            tenant_id="tnt_h",
            pool="sync",
            run_id="run_1",
            estimated_tokens=200,
            deadline_seconds=0.05,
        )
        assert result.reservation_key is not None
        await asyncio.sleep(0.2)  # past the deadline, well inside the reservation's Redis TTL

        swept = await quota.sweep_once()

        assert swept == 1
        assert await redis_client.get("quota:tnt_h:sync") == b"0"
        assert await redis_client.get(result.reservation_key) is None

    @pytest.mark.asyncio
    async def test_does_not_touch_a_reservation_before_its_deadline(
        self, quota: QuotaManager, redis_client: Redis
    ) -> None:
        result = await quota.reserve(
            tenant_id="tnt_i",
            pool="sync",
            run_id="run_1",
            estimated_tokens=200,
            deadline_seconds=60,
        )
        assert result.reservation_key is not None

        swept = await quota.sweep_once()

        assert swept == 0
        assert await redis_client.get("quota:tnt_i:sync") == b"200"
        assert await redis_client.get(result.reservation_key) is not None

    @pytest.mark.asyncio
    async def test_sweeping_after_normal_reconcile_is_a_harmless_no_op(
        self, quota: QuotaManager, redis_client: Redis
    ) -> None:
        """Regression guard for the race the module docstring claims is
        impossible: reconcile happens first (normal path), then the
        sweeper's next pass finds nothing to do — it must not re-apply a
        refund on top of an already-settled reservation."""
        result = await quota.reserve(
            tenant_id="tnt_j",
            pool="sync",
            run_id="run_1",
            estimated_tokens=200,
            deadline_seconds=0.05,
        )
        assert result.reservation_key is not None
        await quota.reconcile(result.reservation_key, actual_tokens=150)
        await asyncio.sleep(0.2)

        swept = await quota.sweep_once()

        assert swept == 0
        assert await redis_client.get("quota:tnt_j:sync") == b"150"
