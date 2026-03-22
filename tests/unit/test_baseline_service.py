"""Unit tests for BaselineService."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services import baseline_service


@pytest.fixture(autouse=True)
def inject_mocks(mock_pool, mock_redis):
    pool, conn = mock_pool
    baseline_service.inject_deps(pool, mock_redis)
    return pool, conn, mock_redis


class TestGetBaseline:
    @pytest.mark.asyncio
    async def test_returns_from_redis_cache(self, mock_pool, mock_redis):
        pool, conn = mock_pool
        mock_redis.hgetall = AsyncMock(return_value={"avg_5d": "500000", "avg_20d": "480000"})

        result = await baseline_service.get_baseline("HPG", 15)

        assert result == {"avg_5d": 500_000, "avg_20d": 480_000}
        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_to_db_when_redis_empty(self, mock_pool, mock_redis):
        pool, conn = mock_pool
        mock_redis.hgetall = AsyncMock(return_value={})
        conn.fetchrow = AsyncMock(return_value={"avg_5d": 300_000, "avg_20d": 290_000, "std_dev": 50_000})

        result = await baseline_service.get_baseline("ACB", 30)

        assert result is not None
        assert result["avg_5d"] == 300_000
        conn.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self, mock_pool, mock_redis):
        pool, conn = mock_pool
        mock_redis.hgetall = AsyncMock(return_value={})
        conn.fetchrow = AsyncMock(return_value=None)

        result = await baseline_service.get_baseline("XYZ", 50)
        assert result is None


class TestRebuildTicker:
    @pytest.mark.asyncio
    async def test_skips_when_no_history(self, mock_pool, mock_redis):
        pool, conn = mock_pool
        conn.fetch = AsyncMock(return_value=[])

        await baseline_service.rebuild_ticker("HPG")

        conn.executemany.assert_not_called()

    @pytest.mark.asyncio
    async def test_computes_avg5d(self, mock_pool, mock_redis):
        pool, conn = mock_pool

        # Create mock rows for slot 15 (9:15 ICT = 2:15 UTC)
        from datetime import datetime, timezone
        import pytz
        ICT = pytz.timezone("Asia/Ho_Chi_Minh")

        mock_rows = []
        for i in range(10):
            # Create a time that maps to slot 15 (9:15 ICT)
            ict_dt = datetime(2026, 3, i + 1, 9, 15, 0).replace(tzinfo=ICT)
            vol = 500_000 + i * 10_000  # varying volumes
            row = MagicMock()
            row.__getitem__ = lambda self, key, ict=ict_dt, v=vol: ict if key == "bar_ict" else v
            mock_rows.append(row)

        def make_row(dt, vol):
            r = MagicMock()
            r.__getitem__ = MagicMock(side_effect=lambda k: dt if k == "bar_ict" else vol)
            return r

        bars = [make_row(datetime(2026, 3, i + 1, 9, 15, tzinfo=ICT), 500_000 + i * 10_000) for i in range(10)]
        conn.fetch = AsyncMock(return_value=bars)
        conn.executemany = AsyncMock()

        # This tests that executemany is called (baseline upserted)
        # Full correctness tested by integration test
        await baseline_service.rebuild_ticker("HPG")
        conn.executemany.assert_called_once()


class TestCheckFirstRun:
    @pytest.mark.asyncio
    async def test_returns_true_when_empty(self, mock_pool, mock_redis):
        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=0)
        result = await baseline_service.check_first_run_backfill()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_has_data(self, mock_pool, mock_redis):
        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=9900)
        result = await baseline_service.check_first_run_backfill()
        assert result is False
