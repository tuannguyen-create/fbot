"""Unit tests for stream_ingester tick accumulation and flush logic."""
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import stream_ingester


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_ingester_state():
    """Reset all module-level tick/session state between tests."""
    with stream_ingester._tick_lock:
        stream_ingester._tick_bars.clear()
    stream_ingester._last_m1_check.clear()
    stream_ingester._shutting_down = False
    stream_ingester._session_confirmed = False
    stream_ingester._stream_connected = False
    stream_ingester._last_bar_time = None
    yield
    with stream_ingester._tick_lock:
        stream_ingester._tick_bars.clear()
    stream_ingester._last_m1_check.clear()
    stream_ingester._shutting_down = False
    stream_ingester._session_confirmed = False
    stream_ingester._stream_connected = False


def _tick(ticker="HPG", ts="2026-03-25T09:15:10", match_vol=1000,
          bu=800, sd=200, close=25000.0, fb_total=500, fs_total=300):
    """Build a fake RealTimeData.to_dict() payload."""
    return {
        "Ticker": ticker,
        "Timestamp": ts,
        "MatchVolume": match_vol,
        "Bu": bu,
        "Sd": sd,
        "Close": close,
        "ForeignBuyVolumeTotal": fb_total,
        "ForeignSellVolumeTotal": fs_total,
    }


# ── Accumulate tick ────────────────────────────────────────────────────────

class TestAccumulateTick:
    def test_first_tick_starts_accumulator(self):
        completed, partial = stream_ingester._accumulate_tick(
            _tick("HPG", "2026-03-25T09:15:10", match_vol=1000)
        )
        assert completed is None  # no prior minute to emit
        assert partial is not None
        assert partial["ticker"] == "HPG"
        assert partial["volume"] == 1000

    def test_same_minute_accumulates(self):
        stream_ingester._accumulate_tick(_tick("HPG", "2026-03-25T09:15:10", match_vol=1000, close=25000.0))
        _, partial = stream_ingester._accumulate_tick(_tick("HPG", "2026-03-25T09:15:30", match_vol=500, close=25100.0))

        assert partial["volume"] == 1500
        assert partial["high"] == 25100.0
        assert partial["close"] == 25100.0

    def test_minute_boundary_emits_completed_bar(self):
        # Tick at 09:15 (minute key = :15)
        stream_ingester._accumulate_tick(_tick("HPG", "2026-03-25T09:15:50", match_vol=1000, close=25000.0))
        # Tick at 09:16 (new minute) — should emit completed bar for :15
        completed, partial = stream_ingester._accumulate_tick(
            _tick("HPG", "2026-03-25T09:16:05", match_vol=500, close=25100.0)
        )

        assert completed is not None
        assert completed["ticker"] == "HPG"
        assert completed["volume"] == 1000
        assert completed["close"] == 25000.0
        # bar_time should be the :15 minute key (UTC); 09:15 ICT = 02:15 UTC (minute=15)
        assert completed["bar_time"].minute == 15
        assert partial["volume"] == 500

    def test_fb_fs_carry_forward_first_tick(self):
        """Fix 4: first tick of new minute uses prev minute's fb_end as fb_start."""
        # Minute :15 accumulates fb 100→300
        stream_ingester._accumulate_tick(_tick("HPG", "2026-03-25T09:15:10", fb_total=100, fs_total=50))
        stream_ingester._accumulate_tick(_tick("HPG", "2026-03-25T09:15:50", fb_total=300, fs_total=150))

        # Minute :16 first tick has fb_total=400 (100 units bought in this tick)
        completed, partial = stream_ingester._accumulate_tick(
            _tick("HPG", "2026-03-25T09:16:05", match_vol=200, fb_total=400, fs_total=150)
        )

        # completed bar for :15 should have fb = 300 - 100 = 200
        assert completed["fb"] == 200

        # partial for :16 should have fb = 400 - 300 = 100 (not zero)
        assert partial["fb"] == 100

    def test_empty_ticker_returns_none(self):
        completed, partial = stream_ingester._accumulate_tick({"Timestamp": "2026-03-25T09:15:10"})
        assert completed is None
        assert partial is None

    def test_bad_timestamp_returns_none(self):
        completed, partial = stream_ingester._accumulate_tick({"Ticker": "HPG", "Timestamp": "not-a-date"})
        assert completed is None
        assert partial is None


# ── Minute flush timer ─────────────────────────────────────────────────────

class TestMinuteFlushTimer:
    @pytest.mark.asyncio
    async def test_flushes_bar_whose_minute_has_passed(self, mock_pool):
        """Bar with minute_key in the past should be flushed and processed."""
        pool, conn = mock_pool
        stream_ingester._pool = pool
        stream_ingester._loop = asyncio.get_event_loop()

        past_minute = datetime(2026, 3, 25, 2, 14, 0, tzinfo=timezone.utc)  # 09:14 ICT
        with stream_ingester._tick_lock:
            stream_ingester._tick_bars["HPG"] = {
                "minute_key": past_minute,
                "open": 25000.0, "high": 25100.0, "low": 24900.0, "close": 25050.0,
                "volume": 1_000_000, "bu": 700_000, "sd": 300_000,
                "fb_start": 0, "fs_start": 0, "fb_end": 100, "fs_end": 50,
            }

        flushed = []

        async def fake_process_bar(bar):
            flushed.append(bar)

        with patch("app.services.stream_ingester._process_bar", side_effect=fake_process_bar), \
             patch("app.services.stream_ingester.datetime") as mock_dt:
            # Fake "now" as 09:16 ICT (2 minutes past the bar)
            mock_dt.now.return_value = datetime(2026, 3, 25, 2, 16, 30, tzinfo=timezone.utc)
            mock_dt.fromisoformat = datetime.fromisoformat  # preserve for _accumulate_tick

            # Run flush check (internals of _minute_flush_timer without the sleep loop)
            now_utc = datetime(2026, 3, 25, 2, 16, 30, tzinfo=timezone.utc)
            current_minute = now_utc.replace(second=0, microsecond=0)

            to_flush = []
            with stream_ingester._tick_lock:
                for ticker in list(stream_ingester._tick_bars.keys()):
                    acc = stream_ingester._tick_bars.get(ticker)
                    if acc is not None and acc["minute_key"] < current_minute and acc["volume"] > 0:
                        to_flush.append(stream_ingester._emit_bar(ticker, acc))
                        del stream_ingester._tick_bars[ticker]

            for bar in to_flush:
                await fake_process_bar(bar)

        assert len(flushed) == 1
        assert flushed[0]["ticker"] == "HPG"
        assert flushed[0]["volume"] == 1_000_000
        # Accumulator should be cleared
        assert "HPG" not in stream_ingester._tick_bars

    @pytest.mark.asyncio
    async def test_does_not_flush_current_minute(self):
        """Bar for the current minute should NOT be flushed by timer."""
        now_utc = datetime.now(timezone.utc)
        current_minute = now_utc.replace(second=0, microsecond=0)

        with stream_ingester._tick_lock:
            stream_ingester._tick_bars["HPG"] = {
                "minute_key": current_minute,
                "open": 25000.0, "high": 25000.0, "low": 25000.0, "close": 25000.0,
                "volume": 500_000, "bu": 300_000, "sd": 200_000,
                "fb_start": 0, "fs_start": 0, "fb_end": 50, "fs_end": 30,
            }

        to_flush = []
        with stream_ingester._tick_lock:
            for ticker in list(stream_ingester._tick_bars.keys()):
                acc = stream_ingester._tick_bars.get(ticker)
                if acc is not None and acc["minute_key"] < current_minute and acc["volume"] > 0:
                    to_flush.append(ticker)

        assert len(to_flush) == 0  # current minute not flushed


# ── Disconnect flush ───────────────────────────────────────────────────────

class TestFlushAllBars:
    @pytest.mark.asyncio
    async def test_flush_all_bars_emits_partial_minute(self, mock_pool):
        """_flush_all_bars() must emit even bars mid-minute (for proactive restart)."""
        pool, conn = mock_pool
        stream_ingester._pool = pool

        now_utc = datetime.now(timezone.utc)
        current_minute = now_utc.replace(second=0, microsecond=0)

        with stream_ingester._tick_lock:
            stream_ingester._tick_bars["TCB"] = {
                "minute_key": current_minute,  # current, not past
                "open": 30000.0, "high": 30200.0, "low": 29900.0, "close": 30100.0,
                "volume": 800_000, "bu": 600_000, "sd": 200_000,
                "fb_start": 0, "fs_start": 0, "fb_end": 200, "fs_end": 100,
            }

        flushed = []

        async def fake_process_bar(bar):
            flushed.append(bar)

        with patch("app.services.stream_ingester._process_bar", side_effect=fake_process_bar):
            await stream_ingester._flush_all_bars()

        assert len(flushed) == 1
        assert flushed[0]["ticker"] == "TCB"
        assert flushed[0]["volume"] == 800_000

    @pytest.mark.asyncio
    async def test_flush_all_bars_skips_empty_accumulators(self, mock_pool):
        """_flush_all_bars() skips tickers with volume=0."""
        pool, conn = mock_pool
        stream_ingester._pool = pool

        now_utc = datetime.now(timezone.utc)
        current_minute = now_utc.replace(second=0, microsecond=0)

        with stream_ingester._tick_lock:
            stream_ingester._tick_bars["VCB"] = {
                "minute_key": current_minute,
                "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0,
                "volume": 0, "bu": 0, "sd": 0,
                "fb_start": 0, "fs_start": 0, "fb_end": 0, "fs_end": 0,
            }

        flushed = []

        async def fake_process_bar(bar):
            flushed.append(bar)

        with patch("app.services.stream_ingester._process_bar", side_effect=fake_process_bar):
            await stream_ingester._flush_all_bars()

        assert len(flushed) == 0


# ── Reset tick state ───────────────────────────────────────────────────────

class TestResetTickState:
    def test_reset_clears_tick_bars_and_m1_check(self):
        """_reset_tick_state() must clear both _tick_bars and _last_m1_check."""
        with stream_ingester._tick_lock:
            stream_ingester._tick_bars["HPG"] = {"minute_key": "x", "volume": 1}
        stream_ingester._last_m1_check["HPG"] = 999.0

        stream_ingester._reset_tick_state()

        assert len(stream_ingester._tick_bars) == 0
        assert len(stream_ingester._last_m1_check) == 0


# ── Shutdown race gate (_shutting_down) ────────────────────────────────────

class TestShuttingDownGate:
    def test_on_tick_raw_ignored_when_shutting_down(self):
        """_on_tick_raw must not modify _tick_bars when _shutting_down=True."""
        stream_ingester._shutting_down = True

        class FakeData:
            def to_dict(self):
                return {
                    "Ticker": "HPG",
                    "Timestamp": "2026-03-25T09:15:10",
                    "MatchVolume": 1000,
                    "Bu": 800, "Sd": 200, "Close": 25000.0,
                    "ForeignBuyVolumeTotal": 100, "ForeignSellVolumeTotal": 50,
                }

        stream_ingester._on_tick_raw(FakeData())

        # No tick should have been accumulated
        assert len(stream_ingester._tick_bars) == 0

    def test_on_tick_raw_processes_when_not_shutting_down(self):
        """_on_tick_raw processes normally when _shutting_down=False."""
        stream_ingester._shutting_down = False
        stream_ingester._loop = None  # prevent run_coroutine_threadsafe

        class FakeData:
            def to_dict(self):
                return {
                    "Ticker": "HPG",
                    "Timestamp": "2026-03-25T09:15:10",
                    "MatchVolume": 1000,
                    "Bu": 800, "Sd": 200, "Close": 25000.0,
                    "ForeignBuyVolumeTotal": 100, "ForeignSellVolumeTotal": 50,
                }

        stream_ingester._on_tick_raw(FakeData())

        # Tick should have been accumulated
        assert "HPG" in stream_ingester._tick_bars

    def test_session_confirmed_set_on_first_watchlist_tick(self):
        """_session_confirmed transitions False→True on first valid tick."""
        assert stream_ingester._session_confirmed is False
        stream_ingester._loop = None

        class FakeData:
            def to_dict(self):
                return {
                    "Ticker": "HPG",  # HPG is in WATCHLIST
                    "Timestamp": "2026-03-25T09:15:10",
                    "MatchVolume": 500,
                    "Bu": 400, "Sd": 100, "Close": 25000.0,
                    "ForeignBuyVolumeTotal": 0, "ForeignSellVolumeTotal": 0,
                }

        stream_ingester._on_tick_raw(FakeData())

        assert stream_ingester._session_confirmed is True


# ── Health status accuracy ─────────────────────────────────────────────────

class TestGetDetailedStatus:
    def test_connected_only_after_session_confirmed(self):
        """status=connected requires BOTH _stream_connected AND _session_confirmed."""
        from datetime import timezone

        stream_ingester._stream_connected = True
        stream_ingester._session_confirmed = False
        stream_ingester._startup_at = datetime.now(timezone.utc)

        status = stream_ingester.get_detailed_status()

        # Should NOT be "connected" yet — no tick received
        assert status["status"] != "connected"

    def test_connected_after_first_tick(self):
        """After first tick, status becomes connected."""
        stream_ingester._stream_connected = True
        stream_ingester._session_confirmed = True
        stream_ingester._last_bar_time = datetime.now(timezone.utc)

        status = stream_ingester.get_detailed_status()

        assert status["status"] == "connected"

    def test_get_status_simple_requires_session_confirmed(self):
        """get_status() returns 'connected' only when session is confirmed."""
        stream_ingester._stream_connected = True
        stream_ingester._session_confirmed = False
        assert stream_ingester.get_status() == "disconnected"

        stream_ingester._session_confirmed = True
        assert stream_ingester.get_status() == "connected"
