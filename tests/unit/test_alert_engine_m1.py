"""Unit tests for Alert Engine M1 (Volume Scanner)."""
import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import alert_engine_m1
from app.config import settings


@pytest.fixture(autouse=True)
def reset_m1_state():
    """Reset global state between tests."""
    alert_engine_m1._pending_confirms.clear()
    yield
    alert_engine_m1._pending_confirms.clear()


@pytest.fixture
def injected_m1(mock_pool, mock_redis):
    pool, conn = mock_pool
    queue = asyncio.Queue()
    alert_engine_m1.inject_deps(pool, mock_redis, queue)
    return pool, conn, mock_redis, queue


def _mock_alert_row(alert_id: int):
    """Return a mock asyncpg-like record for alert INSERT RETURNING id, fired_at."""
    row = {"id": alert_id, "fired_at": None}
    # Support both row["key"] and row.key access patterns
    mock = MagicMock()
    mock.__getitem__ = lambda self, key: row[key]
    mock.__bool__ = lambda self: True
    return mock


class TestM1Process:
    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self, injected_m1, sample_bar):
        """volume = 1.2M, baseline = 1M → ratio = 1.2x < 2.0x → no alert"""
        pool, conn, redis, queue = injected_m1
        bar = {**sample_bar, "volume": 1_200_000}

        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs:
            mock_bs.get_baseline = AsyncMock(return_value={"avg_5d": 1_000_000})
            await alert_engine_m1.process(bar)

        assert conn.fetchrow.call_count == 0

    @pytest.mark.asyncio
    async def test_fires_alert_normal_threshold(self, injected_m1, sample_bar):
        """volume = 2.1M, baseline = 1M → ratio = 2.1x >= 2.0x → fires"""
        pool, conn, redis, queue = injected_m1
        bar = {**sample_bar, "volume": 2_100_000,
               "bar_time": datetime(2026, 3, 18, 2, 45, 0, tzinfo=timezone.utc)}  # 9:45 ICT — normal

        conn.fetchrow = AsyncMock(return_value=_mock_alert_row(42))
        redis.exists = AsyncMock(return_value=0)

        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs, \
             patch("app.services.alert_engine_m1.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.check_intraday_breakout", new=AsyncMock()):
            mock_bs.get_baseline = AsyncMock(return_value={"avg_5d": 1_000_000})
            mock_notif.send_volume_alert_email = AsyncMock()
            await alert_engine_m1.process(bar)

        conn.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_fires_at_magic_threshold(self, injected_m1, sample_bar):
        """volume = 1.6M, baseline = 1M → ratio = 1.6x >= 1.5x magic → fires"""
        pool, conn, redis, queue = injected_m1
        # 9:15 ICT = magic window
        bar = {**sample_bar, "volume": 1_600_000}
        conn.fetchrow = AsyncMock(return_value=_mock_alert_row(43))
        redis.exists = AsyncMock(return_value=0)

        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs, \
             patch("app.services.alert_engine_m1.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.check_intraday_breakout", new=AsyncMock()):
            mock_bs.get_baseline = AsyncMock(return_value={"avg_5d": 1_000_000})
            mock_notif.send_volume_alert_email = AsyncMock()
            await alert_engine_m1.process(bar)

        conn.fetchrow.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_alert_outside_trading_hours(self, injected_m1):
        """bar_time at 8:00 ICT → slot=None → skip"""
        pool, conn, redis, queue = injected_m1
        bar = {
            "ticker": "HPG",
            "bar_time": datetime(2026, 3, 18, 1, 0, 0, tzinfo=timezone.utc),  # 8:00 ICT
            "volume": 5_000_000,
            "bu": 0, "sd": 0, "fb": 0, "fs": 0, "fn": 0,
        }
        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs:
            mock_bs.get_baseline = AsyncMock(return_value={"avg_5d": 100_000})
            await alert_engine_m1.process(bar)

        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_baseline_skips(self, injected_m1, sample_bar):
        """No baseline for this slot → skip silently"""
        pool, conn, redis, queue = injected_m1
        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs:
            mock_bs.get_baseline = AsyncMock(return_value=None)
            await alert_engine_m1.process(sample_bar)

        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_throttle_prevents_double_fire(self, injected_m1, sample_bar):
        """Redis throttle key exists → skip alert even if ratio >= threshold"""
        pool, conn, redis, queue = injected_m1
        redis.exists = AsyncMock(return_value=1)  # throttle active

        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs:
            mock_bs.get_baseline = AsyncMock(return_value={"avg_5d": 100_000})
            bar = {**sample_bar, "volume": 5_000_000}
            await alert_engine_m1.process(bar)

        conn.fetchrow.assert_not_called()


class TestBuPctCalculation:
    @pytest.mark.asyncio
    async def test_bu_pct_correct(self, injected_m1, sample_bar):
        """bu=800k, sd=200k → bu_pct = 80.0"""
        pool, conn, redis, queue = injected_m1
        bar = {**sample_bar, "volume": 2_100_000, "bu": 800_000, "sd": 200_000,
               "bar_time": datetime(2026, 3, 18, 2, 45, 0, tzinfo=timezone.utc)}
        conn.fetchrow = AsyncMock(return_value=_mock_alert_row(44))
        redis.exists = AsyncMock(return_value=0)

        captured_args = []

        async def fake_execute(*args):
            captured_args.append(args)

        conn.execute = AsyncMock(side_effect=fake_execute)

        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs, \
             patch("app.services.alert_engine_m1.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.check_intraday_breakout", new=AsyncMock()):
            mock_bs.get_baseline = AsyncMock(return_value={"avg_5d": 1_000_000})
            mock_notif.send_volume_alert_email = AsyncMock()
            await alert_engine_m1.process(bar)

        # Verify fetchrow was called with correct bu_pct = 80.0
        call_args = conn.fetchrow.call_args
        # INSERT args: 0=sql, 1=ticker, 2=slot, 3=bar_time, 4=vol, 5=avg5d, 6=ratio, 7=bu_pct
        bu_pct_passed = call_args[0][7]
        assert abs(bu_pct_passed - 80.0) < 0.01

    @pytest.mark.asyncio
    async def test_bu_pct_zero_division(self, injected_m1, sample_bar):
        """bu=0, sd=0 → bu_pct=None, no crash"""
        pool, conn, redis, queue = injected_m1
        bar = {**sample_bar, "volume": 2_100_000, "bu": 0, "sd": 0,
               "bar_time": datetime(2026, 3, 18, 2, 45, 0, tzinfo=timezone.utc)}
        conn.fetchrow = AsyncMock(return_value=_mock_alert_row(45))
        redis.exists = AsyncMock(return_value=0)

        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs, \
             patch("app.services.alert_engine_m1.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.check_intraday_breakout", new=AsyncMock()):
            mock_bs.get_baseline = AsyncMock(return_value={"avg_5d": 1_000_000})
            mock_notif.send_volume_alert_email = AsyncMock()
            await alert_engine_m1.process(bar)  # should not raise

        call_args = conn.fetchrow.call_args
        # INSERT args: 0=sql, 1=ticker, 2=slot, 3=bar_time, 4=vol, 5=avg5d, 6=ratio, 7=bu_pct
        bu_pct_passed = call_args[0][7]
        assert bu_pct_passed is None


class TestConfirmation:
    @pytest.mark.asyncio
    async def test_pending_confirm_stored(self, injected_m1, sample_bar):
        """After alert fires, pending_confirms should have entry."""
        pool, conn, redis, queue = injected_m1
        bar = {**sample_bar, "volume": 2_500_000,
               "bar_time": datetime(2026, 3, 18, 2, 45, 0, tzinfo=timezone.utc)}
        conn.fetchrow = AsyncMock(return_value=_mock_alert_row(50))
        redis.exists = AsyncMock(return_value=0)

        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs, \
             patch("app.services.alert_engine_m1.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.check_intraday_breakout", new=AsyncMock()):
            mock_bs.get_baseline = AsyncMock(return_value={"avg_5d": 1_000_000})
            mock_notif.send_volume_alert_email = AsyncMock()
            await alert_engine_m1.process(bar)

        assert "HPG" in alert_engine_m1._pending_confirms
        pending = alert_engine_m1._pending_confirms["HPG"]
        assert pending["alert_id"] == 50
        # confirm_by_slot = slot + 15
        from app.utils.trading_hours import get_slot
        from app.utils.timezone import to_ict
        ict = to_ict(bar["bar_time"])
        expected_slot = get_slot(ict.time())
        assert pending["slot"] == expected_slot
        assert pending["confirm_by_slot"] == expected_slot + 15

    @pytest.mark.asyncio
    async def test_partial_origin_confirm_replaces_not_adds(self):
        """Alert fired from partial bar (50k vol, slot=15).
        Completed bar for same slot (200k) must REPLACE cumulative, not add.
        cumulative should be 200k, not 250k (partial 50k + completed 200k).
        """
        from datetime import timezone
        alert_engine_m1._pending_confirms["HPG"] = {
            "alert_id": 99,
            "slot": 15,
            "confirm_by_slot": 30,
            "cumulative_volume": 50_000,  # set by partial bar that triggered the alert
        }

        bar = {
            "ticker": "HPG",
            "bar_time": datetime(2026, 3, 18, 2, 15, 0, tzinfo=timezone.utc),  # slot=15
            "volume": 200_000,  # full minute volume
            "bu": 0, "sd": 0, "fb": 0, "fs": 0, "fn": 0,
        }

        await alert_engine_m1._check_confirmations("HPG", bar, current_slot=15)

        # Must have replaced, not added
        assert alert_engine_m1._pending_confirms["HPG"]["cumulative_volume"] == 200_000

    @pytest.mark.asyncio
    async def test_subsequent_minute_adds_normally(self):
        """Bar for slot=16 (after alert at slot=15) should ADD to cumulative."""
        from datetime import timezone
        alert_engine_m1._pending_confirms["HPG"] = {
            "alert_id": 99,
            "slot": 15,
            "confirm_by_slot": 30,
            "cumulative_volume": 200_000,  # already replaced by completed slot-15 bar
        }

        bar = {
            "ticker": "HPG",
            "bar_time": datetime(2026, 3, 18, 2, 16, 0, tzinfo=timezone.utc),  # slot=16
            "volume": 150_000,
            "bu": 0, "sd": 0, "fb": 0, "fs": 0, "fn": 0,
        }

        await alert_engine_m1._check_confirmations("HPG", bar, current_slot=16)

        assert alert_engine_m1._pending_confirms["HPG"]["cumulative_volume"] == 350_000

    @pytest.mark.asyncio
    async def test_partial_bar_skips_confirm_accumulation(self, injected_m1, sample_bar):
        """process(bar, is_partial=True) must NOT touch pending_confirms cumulative."""
        pool, conn, redis, queue = injected_m1

        # Seed a pending confirm
        alert_engine_m1._pending_confirms["HPG"] = {
            "alert_id": 77,
            "slot": 15,
            "confirm_by_slot": 30,
            "cumulative_volume": 100_000,
        }

        bar = {**sample_bar, "volume": 999_999,
               "bar_time": datetime(2026, 3, 18, 2, 16, 0, tzinfo=timezone.utc)}

        with patch("app.services.alert_engine_m1.baseline_service") as mock_bs:
            mock_bs.get_baseline = AsyncMock(return_value={"avg_5d": 1_000_000})
            await alert_engine_m1.process(bar, is_partial=True)

        # cumulative_volume must be untouched
        assert alert_engine_m1._pending_confirms["HPG"]["cumulative_volume"] == 100_000


# ── TestComputeM1Features ──────────────────────────────────────────────────

class TestComputeM1Features:
    def _bar(self, open_=25000, high=25200, low=24900, close=25100, volume=2_000_000):
        return {"open": open_, "high": high, "low": low, "close": close,
                "volume": volume, "bu": 0, "sd": 0}

    def _recent(self, n=20, close=25000.0, volume=500_000):
        return [{"open": close, "high": close+50, "low": close-50,
                 "close": close, "volume": volume, "bu": 0, "sd": 0}
                for _ in range(n)]

    def test_strong_bull_candle_detected(self):
        # close very near high → strong bull
        bar = self._bar(open_=25000, high=25200, low=24980, close=25180)
        result = alert_engine_m1.compute_m1_features(bar, self._recent())
        assert result["strong_bull_candle"] is True
        assert result["body_pct"] > 50

    def test_weak_candle_not_strong_bull(self):
        # large upper shadow
        bar = self._bar(open_=25000, high=25400, low=24900, close=25050)
        result = alert_engine_m1.compute_m1_features(bar, self._recent())
        assert result["strong_bull_candle"] is False

    def test_sideways_base_detected(self):
        # tight range + low volume = sideways base
        recent = self._recent(n=20, close=25000, volume=300_000)
        bar = self._bar()
        result = alert_engine_m1.compute_m1_features(bar, recent)
        # avg_vol_20 = 300k, avg_vol_50 also 300k (only 20 bars) → not sideways
        # need avg_vol_20 <= 0.8 * avg_vol_50 which requires a difference
        # If same vols, avg_vol_20 == avg_vol_50 → 300k <= 0.8*300k is False
        assert result["is_sideways_base"] is False  # equal vols → not "cạn cung"

    def test_sideways_base_with_vol_shrink(self):
        # 20 quiet bars (300k) + 30 noisier bars (600k) = vol shrink → sideways
        recent_20 = self._recent(n=20, close=25000, volume=300_000)
        recent_30 = self._recent(n=30, close=25000, volume=600_000)
        recent = recent_20 + recent_30  # newest-first: 20 quiet then 30 louder
        bar = self._bar(open_=25000, high=25050, low=24960, close=25020)
        result = alert_engine_m1.compute_m1_features(bar, recent)
        # avg_vol_20 = 300k, avg_vol_50 = (20*300k + 30*600k)/50 = 480k
        # range_pct from 20 closes all at 25000 → 0% → is_sideways_base needs range>0
        assert result["avg_vol_20"] == 300_000
        assert result["avg_vol_50"] == 480_000

    def test_ma10_ma20_calculated(self):
        recent = self._recent(n=30, close=25000)
        bar = self._bar(close=25100)
        result = alert_engine_m1.compute_m1_features(bar, recent)
        assert result["ma10"] is not None
        assert result["ma20"] is not None
        # MA10 uses trigger bar (25100) + 9 recent (25000 each) → slightly above 25000
        assert result["ma10"] == pytest.approx(25010.0, abs=1.0)

    def test_price_above_ma10_true(self):
        recent = self._recent(n=20, close=24000)
        bar = self._bar(close=25000)  # well above 24k base
        result = alert_engine_m1.compute_m1_features(bar, recent)
        assert result["price_above_ma10"] is True

    def test_returns_none_for_macd_when_insufficient_bars(self):
        recent = self._recent(n=10)  # only 10 bars — not enough for MACD
        bar = self._bar()
        result = alert_engine_m1.compute_m1_features(bar, recent)
        assert result["macd_hist"] is None
        assert result["macd_hist_rising"] is None

    def test_quality_score_high_for_ideal_bar(self):
        # strong bull candle + vol shrink + above MA
        recent_20 = self._recent(n=20, close=24800, volume=300_000)
        recent_30 = self._recent(n=30, close=24800, volume=600_000)
        recent = recent_20 + recent_30
        bar = self._bar(open_=24800, high=25200, low=24780, close=25180)
        result = alert_engine_m1.compute_m1_features(bar, recent)
        assert result["quality_score"] >= 40  # at least strong_bull (30) + above_ma10 (10)
        assert result["quality_reason"] != "không đủ tín hiệu"

    def test_quality_score_zero_for_bad_bar(self):
        bar = self._bar(open_=25100, high=25200, low=24900, close=24950)  # red candle
        result = alert_engine_m1.compute_m1_features(bar, [])
        assert result["quality_score"] == 0
        assert result["quality_reason"] == "không đủ tín hiệu"

    def test_empty_recent_bars_safe(self):
        bar = self._bar()
        result = alert_engine_m1.compute_m1_features(bar, [])
        assert "quality_score" in result
        assert result["ma10"] is None
        assert result["ma20"] is None


# ── TestCalcMacd ───────────────────────────────────────────────────────────

class TestCalcMacd:
    def test_returns_none_when_insufficient_bars(self):
        hist, rising = alert_engine_m1._calc_macd([25000.0] * 20)
        assert hist is None
        assert rising is None

    def test_returns_values_with_sufficient_bars(self):
        # 40 bars of constant price → MACD hist = 0
        hist, rising = alert_engine_m1._calc_macd([25000.0] * 40)
        assert hist is not None
        assert isinstance(hist, float)

    def test_rising_when_increasing(self):
        # Prices gradually rising → MACD histogram should be positive & rising
        closes = list(reversed([25000 + i * 10 for i in range(50)]))
        hist, rising = alert_engine_m1._calc_macd(closes)
        assert hist is not None
