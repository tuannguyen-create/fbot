"""Unit tests for daily_ohlcv_service batching behavior."""
import pytest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import daily_ohlcv_service


@pytest.fixture(autouse=True)
def inject_service(mock_pool):
    pool, _ = mock_pool
    daily_ohlcv_service.inject_deps(pool)
    return pool


class TestBackfillHistorical:
    @pytest.mark.asyncio
    async def test_batches_through_all_active_tickers(self):
        tickers = [f"T{i:03d}" for i in range(705)]
        calls = []

        def fake_fetch(batch, days):
            calls.append((list(batch), days))
            return [{"ticker": t, "date": date(2026, 3, 27)} for t in batch]

        async def fake_run_in_executor(_self, _executor, fn):
            return fn()

        fake_loop = type("FakeLoop", (), {"run_in_executor": fake_run_in_executor})()

        with patch("app.services.daily_ohlcv_service.universe_service.get_active_tickers", new=AsyncMock(return_value=tickers)), \
             patch("app.services.daily_ohlcv_service._fetch_historical_blocking", side_effect=fake_fetch), \
             patch("app.services.daily_ohlcv_service._persist_bars", new=AsyncMock(side_effect=lambda bars: len(bars))), \
             patch("app.services.daily_ohlcv_service.asyncio.get_running_loop", return_value=fake_loop), \
             patch.object(daily_ohlcv_service.settings, "FIINQUANT_TICKER_LIMIT", 731):
            total = await daily_ohlcv_service.backfill_historical(days=25)

        assert total == 705
        assert len(calls) == 4
        assert [len(batch) for batch, _ in calls] == [200, 200, 200, 105]
        assert all(days == 25 for _, days in calls)


class TestFetchHistoricalBlocking:
    def test_uses_lowercase_1d_timeframe_for_sdk(self):
        captured = {}

        class FakeEvent:
            def get_data(self):
                return None

        class FakeSession:
            def __init__(self, username, password):
                pass

            def login(self):
                return self

            def Fetch_Trading_Data(self, **kwargs):
                captured.update(kwargs)
                return FakeEvent()

        fake_module = SimpleNamespace(FiinSession=FakeSession)

        with patch.dict("sys.modules", {"FiinQuantX": fake_module}):
            daily_ohlcv_service._fetch_historical_blocking(["HPG"], 5)

        assert captured["by"] == "1d"
