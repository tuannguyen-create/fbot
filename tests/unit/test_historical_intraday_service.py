"""Unit tests for historical_intraday_service."""
import pytest
from datetime import datetime, date, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import historical_intraday_service


@pytest.fixture(autouse=True)
def inject_service(mock_pool):
    pool, conn = mock_pool
    historical_intraday_service.inject_deps(pool)
    return pool, conn


# ── _parse_1m_bar ──────────────────────────────────────────────────────────

class TestParse1mBar:
    def test_parses_ticker_and_timestamp(self):
        raw = {
            "Ticker": "HPG",
            "Timestamp": "2026-03-25T09:15:00",
            "open": 25000.0, "high": 25200.0, "low": 24900.0, "close": 25100.0,
            "volume": 500_000, "bu": 300_000, "sd": 200_000,
            "fb": 50_000, "fs": 30_000, "fn": 20_000,
        }
        result = historical_intraday_service._parse_1m_bar(raw)
        assert result is not None
        assert result["ticker"] == "HPG"
        assert result["volume"] == 500_000
        # 09:15 ICT = 02:15 UTC
        assert result["bar_time"].hour == 2
        assert result["bar_time"].minute == 15
        assert result["bar_time"].tzinfo == timezone.utc

    def test_falls_back_to_datetime_field(self):
        raw = {
            "ticker": "acb",
            "datetime": "2026-03-25T09:15:00",
            "volume": 100_000,
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
        }
        result = historical_intraday_service._parse_1m_bar(raw)
        assert result is not None
        assert result["ticker"] == "ACB"   # uppercased

    def test_returns_none_missing_ticker(self):
        raw = {"Timestamp": "2026-03-25T09:15:00", "volume": 100_000}
        assert historical_intraday_service._parse_1m_bar(raw) is None

    def test_returns_none_missing_timestamp(self):
        raw = {"Ticker": "HPG", "volume": 100_000}
        assert historical_intraday_service._parse_1m_bar(raw) is None

    def test_zero_fills_missing_flow_fields(self):
        raw = {
            "Ticker": "HPG",
            "Timestamp": "2026-03-25T09:15:00",
            "volume": 100_000,
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
        }
        result = historical_intraday_service._parse_1m_bar(raw)
        assert result["bu"] == 0
        assert result["fn"] == 0


# ── check_needs_backfill ───────────────────────────────────────────────────

class TestCheckNeedsBackfill:
    @pytest.mark.asyncio
    async def test_needs_backfill_when_sparse(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=3)
        with patch(
            "app.services.historical_intraday_service.universe_service.get_active_tickers",
            new=AsyncMock(return_value=[f"T{i:02d}" for i in range(20)]),
        ):
            result = await historical_intraday_service.check_needs_backfill()
        assert result is True

    @pytest.mark.asyncio
    async def test_no_backfill_when_enough_data(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=12)
        with patch(
            "app.services.historical_intraday_service.universe_service.get_active_tickers",
            new=AsyncMock(return_value=[f"T{i:02d}" for i in range(20)]),
        ):
            result = await historical_intraday_service.check_needs_backfill()
        assert result is False

    @pytest.mark.asyncio
    async def test_needs_backfill_when_empty(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=0)
        with patch(
            "app.services.historical_intraday_service.universe_service.get_active_tickers",
            new=AsyncMock(return_value=[f"T{i:02d}" for i in range(20)]),
        ):
            result = await historical_intraday_service.check_needs_backfill()
        assert result is True


# ── _upsert_intraday ──────────────────────────────────────────────────────

class TestUpsertIntraday:
    @pytest.mark.asyncio
    async def test_upserts_bars(self, mock_pool):
        pool, conn = mock_pool
        conn.executemany = AsyncMock()
        bars = [
            {
                "ticker": "HPG", "bar_time": datetime(2026, 3, 25, 2, 15, tzinfo=timezone.utc),
                "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                "volume": 100_000, "bu": 0, "sd": 0, "fb": 0, "fs": 0, "fn": 0,
            }
        ]
        count = await historical_intraday_service._upsert_intraday(bars)
        assert count == 1
        conn.executemany.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_empty_list(self, mock_pool):
        pool, conn = mock_pool
        conn.executemany = AsyncMock()
        count = await historical_intraday_service._upsert_intraday([])
        assert count == 0
        conn.executemany.assert_not_called()


# ── backfill_intraday ─────────────────────────────────────────────────────

class TestBackfillIntraday:
    @pytest.mark.asyncio
    async def test_calls_fetch_and_upsert(self, mock_pool):
        pool, conn = mock_pool
        conn.executemany = AsyncMock()
        conn.fetchval = AsyncMock(return_value=0)   # empty baselines

        fake_bar = {
            "ticker": "HPG", "bar_time": datetime(2026, 3, 25, 2, 15, tzinfo=timezone.utc),
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
            "volume": 100_000, "bu": 0, "sd": 0, "fb": 0, "fs": 0, "fn": 0,
        }

        with patch.object(
            historical_intraday_service, "_fetch_1m_blocking", return_value=[fake_bar]
        ), patch("app.services.historical_intraday_service.baseline_service") as mock_bs:
            mock_bs.rebuild_all = AsyncMock()
            await historical_intraday_service.backfill_intraday(days=5)

        mock_bs.rebuild_all.assert_awaited_once_with(force=True)

    @pytest.mark.asyncio
    async def test_no_rebuild_when_zero_bars_fetched(self, mock_pool):
        pool, conn = mock_pool
        with patch.object(
            historical_intraday_service, "_fetch_1m_blocking", return_value=[]
        ), patch("app.services.historical_intraday_service.baseline_service") as mock_bs:
            mock_bs.rebuild_all = AsyncMock()
            await historical_intraday_service.backfill_intraday(days=5)

        mock_bs.rebuild_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batches_through_all_active_tickers(self, mock_pool):
        tickers = [f"T{i:03d}" for i in range(705)]
        calls = []

        def fake_fetch(batch, from_date, to_date):
            calls.append((list(batch), from_date, to_date))
            return [{"ticker": t, "bar_time": datetime(2026, 3, 27, 2, 15, tzinfo=timezone.utc)} for t in batch]

        async def fake_run_in_executor(_self, _executor, fn):
            return fn()

        fake_loop = type("FakeLoop", (), {"run_in_executor": fake_run_in_executor})()

        with patch(
            "app.services.historical_intraday_service.universe_service.get_active_tickers",
            new=AsyncMock(return_value=tickers),
        ), patch.object(historical_intraday_service, "_fetch_1m_blocking", side_effect=fake_fetch), \
             patch.object(historical_intraday_service, "_upsert_intraday", new=AsyncMock(side_effect=lambda bars: len(bars))), \
             patch("app.services.historical_intraday_service.baseline_service") as mock_bs, \
             patch("app.services.historical_intraday_service.asyncio.get_running_loop", return_value=fake_loop), \
             patch.object(historical_intraday_service.settings, "FIINQUANT_TICKER_LIMIT", 731), \
             patch.object(historical_intraday_service.settings, "FIINQUANT_INTRADAY_HISTORY_DAYS", 180):
            mock_bs.rebuild_all = AsyncMock()
            total = await historical_intraday_service.backfill_intraday(days=5)

        assert total == 705
        assert len(calls) == 8
        assert [len(batch) for batch, _, _ in calls] == [100, 100, 100, 100, 100, 100, 100, 5]
        mock_bs.rebuild_all.assert_awaited_once_with(force=True)
