"""Unit tests for Alert Engine M3 (Cycle Analysis)."""
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import alert_engine_m3
from app.config import settings


class FakeRow(dict):
    """Dict subclass that supports both row['key'] and row.key access."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def make_daily_rows(n=22, base_volume=1_000_000):
    """Generate n fake daily rows as FakeRow objects."""
    rows = []
    start = date(2026, 2, 1)
    from app.utils.trading_hours import add_trading_days
    d = start
    for i in range(n):
        rows.append(FakeRow({
            "date": d,
            "open": 20000.0,
            "high": 20500.0,
            "low": 19800.0,
            "close": 20200.0,
            "volume": base_volume,
        }))
        d = add_trading_days(d, 1)
    return rows


class TestBreakoutDetection:
    @pytest.mark.asyncio
    async def test_breakout_detected(self, mock_pool):
        """today vol = 4x MA20, price +4% → create cycle"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        rows = make_daily_rows(22, 1_000_000)
        # Override last 2 rows: yesterday normal, today breakout
        rows[-2] = {**rows[-2], "volume": 1_000_000, "close": 20000.0}
        rows[-1] = {**rows[-1], "volume": 4_000_000, "close": 20800.0}  # +4%, vol=4x MA20

        conn.fetch = AsyncMock(return_value=list(reversed(rows)))
        conn.fetchrow = AsyncMock(return_value=None)  # no existing active cycle
        conn.fetchval = AsyncMock(return_value=99)  # new cycle_id

        with patch("app.services.alert_engine_m3.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.is_trading_day", return_value=True):
            mock_notif.send_cycle_breakout_email = AsyncMock()
            await alert_engine_m3._analyze_ticker("HPG")

        conn.fetchval.assert_called_once()  # INSERT into cycle_events

    @pytest.mark.asyncio
    async def test_no_breakout_vol_insufficient(self, mock_pool):
        """vol = 2x MA20 (< 3x) → no breakout"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        rows = make_daily_rows(22, 1_000_000)
        rows[-1] = FakeRow({**rows[-1], "volume": 2_000_000, "close": 20800.0})
        # First fetch = OHLCV (reversed/newest-first), second fetch = active cycles (empty)
        conn.fetch = AsyncMock(side_effect=[list(reversed(rows)), []])
        conn.fetchrow = AsyncMock(return_value=None)
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock()

        with patch("app.services.alert_engine_m3.notification") as mock_notif:
            mock_notif.send_cycle_breakout_email = AsyncMock()
            await alert_engine_m3._analyze_ticker("HPG")

        conn.fetchval.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_breakout_price_insufficient(self, mock_pool):
        """vol = 4x MA20 but price only +1% (< 3%) → no breakout"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        rows = make_daily_rows(22, 1_000_000)
        rows[-2] = FakeRow({**rows[-2], "close": 20000.0})
        rows[-1] = FakeRow({**rows[-1], "volume": 4_000_000, "close": 20200.0})  # only +1%

        conn.fetch = AsyncMock(side_effect=[list(reversed(rows)), []])
        conn.fetchrow = AsyncMock(return_value=None)
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock()

        with patch("app.services.alert_engine_m3.notification") as mock_notif:
            mock_notif.send_cycle_breakout_email = AsyncMock()
            await alert_engine_m3._analyze_ticker("HPG")

        conn.fetchval.assert_not_called()


class TestCycleUpdate:
    @pytest.mark.asyncio
    async def test_10day_warning_sent(self, mock_pool):
        """Cycle with days_remaining=9 (<=10) and alert_sent_10d=False → send warning"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        cycle = {
            "id": 5,
            "ticker": "NVL",
            "breakout_date": date(2026, 3, 1),
            "phase": "distributing",
            "estimated_dist_days": 20,
            "days_remaining": 9,
            "alert_sent_10d": False,
            "alert_sent_bottom": False,
        }
        rows = make_daily_rows(22, 500_000)

        with patch("app.services.alert_engine_m3.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.count_trading_days_between", return_value=11):
            mock_notif.send_cycle_10day_warning_email = AsyncMock()
            conn.execute = AsyncMock()
            await alert_engine_m3._update_cycle("NVL", cycle, rows, 500_000)

        conn.execute.assert_called()
        calls = [str(c) for c in conn.execute.call_args_list]
        assert any("alert_sent_10d" in c for c in calls)

    @pytest.mark.asyncio
    async def test_bottom_detected_low_volume(self, mock_pool):
        """3 consecutive days vol < 50% MA20 + days_remaining<=5 → bottom alert"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        cycle = {
            "id": 6,
            "ticker": "PDR",
            "breakout_date": date(2026, 2, 10),
            "phase": "distributing",
            "estimated_dist_days": 20,
            "days_remaining": 2,
            "alert_sent_10d": True,
            "alert_sent_bottom": False,
        }
        ma20 = 1_000_000
        # Last 3 rows all < 50% MA20 = < 500k
        rows = make_daily_rows(22, 1_000_000)
        rows[-1]["volume"] = 400_000
        rows[-2]["volume"] = 350_000
        rows[-3]["volume"] = 420_000

        with patch("app.services.alert_engine_m3.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.count_trading_days_between", return_value=18):
            mock_notif.send_cycle_bottom_email = AsyncMock()
            conn.execute = AsyncMock()
            await alert_engine_m3._update_cycle("PDR", cycle, rows, ma20)

        # Should call execute with phase='bottoming'
        calls = [str(c) for c in conn.execute.call_args_list]
        assert any("bottoming" in c for c in calls)

    @pytest.mark.asyncio
    async def test_10day_warning_not_sent_if_already_sent(self, mock_pool):
        """alert_sent_10d=True → do not resend"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        cycle = {
            "id": 7,
            "ticker": "KBC",
            "breakout_date": date(2026, 3, 1),
            "phase": "distributing",
            "estimated_dist_days": 20,
            "days_remaining": 8,
            "alert_sent_10d": True,  # Already sent
            "alert_sent_bottom": False,
        }
        rows = make_daily_rows(22, 1_000_000)
        conn.execute = AsyncMock()

        with patch("app.services.alert_engine_m3.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.count_trading_days_between", return_value=12):
            mock_notif.send_cycle_10day_warning_email = AsyncMock()
            await alert_engine_m3._update_cycle("KBC", cycle, rows, 1_000_000)

        mock_notif.send_cycle_10day_warning_email.assert_not_called()
