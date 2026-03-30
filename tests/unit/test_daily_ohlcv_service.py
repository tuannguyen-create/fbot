"""Unit tests for daily_ohlcv_service batching behavior."""
import pytest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import daily_ohlcv_service


@pytest.fixture(autouse=True)
def inject_service(mock_pool):
    pool, _ = mock_pool
    daily_ohlcv_service.inject_deps(pool)
    return pool


class TestBackfillHistorical:
    @pytest.mark.asyncio
    async def test_rest_primary_path(self):
        """When REST returns bars, SDK is never called."""
        tickers = [f"T{i:03d}" for i in range(50)]
        rest_result = SimpleNamespace(
            bars=[{"ticker": t, "date": date(2026, 3, 27)} for t in tickers],
            tickers_with_rows=tickers,
            empty_tickers=[],
            failed_tickers=[],
        )

        async def fake_run_in_executor(_self, _executor, fn):
            return fn()

        fake_loop = type("FakeLoop", (), {"run_in_executor": fake_run_in_executor})()

        with patch("app.services.daily_ohlcv_service.universe_service.get_active_tickers", new=AsyncMock(return_value=tickers)), \
             patch("app.services.fiinquant_rest.fetch_daily_bars_with_status_blocking", return_value=rest_result), \
             patch("app.services.daily_ohlcv_service._persist_bars", new=AsyncMock(side_effect=lambda bars: len(bars))), \
             patch("app.services.daily_ohlcv_service._fetch_historical_blocking") as mock_sdk, \
             patch("app.services.daily_ohlcv_service.asyncio.get_running_loop", return_value=fake_loop):
            total = await daily_ohlcv_service.backfill_historical(days=25)

        assert total == 50
        mock_sdk.assert_not_called()

    @pytest.mark.asyncio
    async def test_sdk_fallback_when_rest_returns_zero(self):
        """When REST returns 0 bars, falls back to SDK batching."""
        tickers = [f"T{i:03d}" for i in range(705)]
        sdk_calls = []
        rest_result = SimpleNamespace(
            bars=[],
            tickers_with_rows=[],
            empty_tickers=tickers,
            failed_tickers=[],
        )

        def fake_fetch(batch, days):
            sdk_calls.append((list(batch), days))
            return [{"ticker": t, "date": date(2026, 3, 27)} for t in batch]

        async def fake_run_in_executor(_self, _executor, fn):
            return fn()

        fake_loop = type("FakeLoop", (), {"run_in_executor": fake_run_in_executor})()

        with patch("app.services.daily_ohlcv_service.universe_service.get_active_tickers", new=AsyncMock(return_value=tickers)), \
             patch("app.services.fiinquant_rest.fetch_daily_bars_with_status_blocking", return_value=rest_result), \
             patch("app.services.daily_ohlcv_service._fetch_historical_blocking", side_effect=fake_fetch), \
             patch("app.services.daily_ohlcv_service._persist_bars", new=AsyncMock(side_effect=lambda bars: len(bars))), \
             patch("app.services.daily_ohlcv_service.asyncio.get_running_loop", return_value=fake_loop), \
             patch.object(daily_ohlcv_service.settings, "FIINQUANT_TICKER_LIMIT", 731):
            total = await daily_ohlcv_service.backfill_historical(days=25)

        assert total == 705
        assert len(sdk_calls) == 4
        assert [len(batch) for batch, _ in sdk_calls] == [200, 200, 200, 105]

    @pytest.mark.asyncio
    async def test_sdk_fallback_when_rest_import_fails(self):
        """When REST module import fails, falls back to SDK."""
        tickers = ["HPG", "VCB"]
        sdk_calls = []

        def fake_fetch(batch, days):
            sdk_calls.append(list(batch))
            return [{"ticker": t, "date": date(2026, 3, 27)} for t in batch]

        async def fake_run_in_executor(_self, _executor, fn):
            return fn()

        fake_loop = type("FakeLoop", (), {"run_in_executor": fake_run_in_executor})()

        with patch("app.services.daily_ohlcv_service.universe_service.get_active_tickers", new=AsyncMock(return_value=tickers)), \
             patch.dict("sys.modules", {"app.services.fiinquant_rest": None}), \
             patch("app.services.daily_ohlcv_service._fetch_historical_blocking", side_effect=fake_fetch), \
             patch("app.services.daily_ohlcv_service._persist_bars", new=AsyncMock(side_effect=lambda bars: len(bars))), \
             patch("app.services.daily_ohlcv_service.asyncio.get_running_loop", return_value=fake_loop), \
             patch.object(daily_ohlcv_service.settings, "FIINQUANT_TICKER_LIMIT", 100):
            total = await daily_ohlcv_service.backfill_historical(days=25)

        assert total == 2
        assert len(sdk_calls) == 1

    @pytest.mark.asyncio
    async def test_sdk_fallback_only_for_rest_missing_tickers(self):
        tickers = ["HPG", "VND", "MBS"]
        rest_result = SimpleNamespace(
            bars=[{"ticker": "HPG", "date": date(2026, 3, 27)}],
            tickers_with_rows=["HPG"],
            empty_tickers=["VND"],
            failed_tickers=["MBS"],
        )
        sdk_calls = []

        def fake_fetch(batch, days):
            sdk_calls.append(list(batch))
            return [{"ticker": t, "date": date(2026, 3, 27)} for t in batch]

        async def fake_run_in_executor(_self, _executor, fn):
            return fn()

        fake_loop = type("FakeLoop", (), {"run_in_executor": fake_run_in_executor})()

        async def fake_persist(bars):
            return len(bars)

        with patch("app.services.daily_ohlcv_service.universe_service.get_active_tickers", new=AsyncMock(return_value=tickers)), \
             patch("app.services.fiinquant_rest.fetch_daily_bars_with_status_blocking", return_value=rest_result), \
             patch("app.services.daily_ohlcv_service._fetch_historical_blocking", side_effect=fake_fetch), \
             patch("app.services.daily_ohlcv_service._persist_bars", new=AsyncMock(side_effect=fake_persist)), \
             patch("app.services.daily_ohlcv_service.asyncio.get_running_loop", return_value=fake_loop), \
             patch.object(daily_ohlcv_service.settings, "FIINQUANT_TICKER_LIMIT", 100):
            summary = await daily_ohlcv_service.backfill_historical(days=25, with_summary=True)

        assert sdk_calls == [["VND", "MBS"]]
        assert summary["bars_upserted"] == 3
        assert summary["rest_tickers_with_rows"] == 1
        assert summary["sdk_fallback_tickers"] == 2


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
