"""Unit tests for Alert Engine M3 (Cycle Analysis)."""
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import alert_engine_m3
from app.services.alert_engine_m3 import PHASE_DISTRIBUTION, PHASE_BOTTOMING, PHASE_INVALIDATED
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


# Patch _get_ticker_meta for all M3 tests — avoids real DB call for eligibility
_MOCK_META = {"eligible": True, "game_type": "institutional"}


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

        with patch("app.services.alert_engine_m3._get_ticker_meta", new=AsyncMock(return_value=_MOCK_META)), \
             patch("app.services.alert_engine_m3.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.is_trading_day", return_value=True):
            mock_notif.send_cycle_breakout_email = AsyncMock()
            await alert_engine_m3._analyze_ticker("HPG")

        # fetchval called twice: M1 alert lookup + INSERT RETURNING id
        assert conn.fetchval.call_count == 2

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

        with patch("app.services.alert_engine_m3._get_ticker_meta", new=AsyncMock(return_value=_MOCK_META)), \
             patch("app.services.alert_engine_m3.notification") as mock_notif:
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

        with patch("app.services.alert_engine_m3._get_ticker_meta", new=AsyncMock(return_value=_MOCK_META)), \
             patch("app.services.alert_engine_m3.notification") as mock_notif:
            mock_notif.send_cycle_breakout_email = AsyncMock()
            await alert_engine_m3._analyze_ticker("HPG")

        conn.fetchval.assert_not_called()

    @pytest.mark.asyncio
    async def test_ineligible_ticker_skipped(self, mock_pool):
        """eligible_for_m3=False → skip entirely"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        with patch("app.services.alert_engine_m3._get_ticker_meta",
                   new=AsyncMock(return_value={"eligible": False, "game_type": "institutional"})):
            await alert_engine_m3._analyze_ticker("HPG")

        conn.fetch.assert_not_called()


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
            "phase": PHASE_DISTRIBUTION,
            "estimated_dist_days": 20,
            "days_remaining": 9,
            "alert_sent_10d": False,
            "alert_sent_bottom": False,
            "breakout_price": None,
            "breakout_zone_low": None,
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
        """3 consecutive days vol < 50% MA20 + days_remaining<=0 → bottoming_candidate"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        cycle = {
            "id": 6,
            "ticker": "PDR",
            "breakout_date": date(2026, 2, 10),
            "phase": PHASE_DISTRIBUTION,
            "estimated_dist_days": 20,
            "days_remaining": 0,
            "alert_sent_10d": True,
            "alert_sent_bottom": False,
            "breakout_price": None,
            "breakout_zone_low": None,
        }
        ma20 = 1_000_000
        # Last 3 rows all < 50% MA20 = < 500k
        rows = make_daily_rows(22, 1_000_000)
        rows[-1]["volume"] = 400_000
        rows[-2]["volume"] = 350_000
        rows[-3]["volume"] = 420_000

        with patch("app.services.alert_engine_m3.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.count_trading_days_between", return_value=20):
            mock_notif.send_cycle_bottom_email = AsyncMock()
            conn.execute = AsyncMock()
            await alert_engine_m3._update_cycle("PDR", cycle, rows, ma20)

        # Should call execute with phase='bottoming_candidate'
        calls = [str(c) for c in conn.execute.call_args_list]
        assert any(PHASE_BOTTOMING in c for c in calls)

    @pytest.mark.asyncio
    async def test_10day_warning_not_sent_if_already_sent(self, mock_pool):
        """alert_sent_10d=True → do not resend"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        cycle = {
            "id": 7,
            "ticker": "KBC",
            "breakout_date": date(2026, 3, 1),
            "phase": PHASE_DISTRIBUTION,
            "estimated_dist_days": 20,
            "days_remaining": 8,
            "alert_sent_10d": True,  # Already sent
            "alert_sent_bottom": False,
            "breakout_price": None,
            "breakout_zone_low": None,
        }
        rows = make_daily_rows(22, 1_000_000)
        conn.execute = AsyncMock()

        with patch("app.services.alert_engine_m3.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.count_trading_days_between", return_value=12):
            mock_notif.send_cycle_10day_warning_email = AsyncMock()
            await alert_engine_m3._update_cycle("KBC", cycle, rows, 1_000_000)

        mock_notif.send_cycle_10day_warning_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidation_on_price_drop(self, mock_pool):
        """close < breakout_zone_low → cycle invalidated"""
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, MagicMock(), None)

        cycle = {
            "id": 8,
            "ticker": "NVL",
            "breakout_date": date(2026, 3, 1),
            "phase": PHASE_DISTRIBUTION,
            "estimated_dist_days": 20,
            "days_remaining": 15,
            "alert_sent_10d": False,
            "alert_sent_bottom": False,
            "breakout_price": 20000.0,
            "breakout_zone_low": 19400.0,  # 3% below 20000
        }
        # today close = 19000 < 19400 zone_low → invalidate
        rows = make_daily_rows(22, 1_000_000)
        rows[-1] = FakeRow({**rows[-1], "close": 19000.0})

        conn.execute = AsyncMock()

        with patch("app.services.alert_engine_m3.count_trading_days_between", return_value=5):
            await alert_engine_m3._update_cycle("NVL", cycle, rows, 1_000_000)

        calls = [str(c) for c in conn.execute.call_args_list]
        assert any(PHASE_INVALIDATED in c for c in calls)


# ── TestReplayHistory ──────────────────────────────────────────────────────

class TestReplayHistory:
    @pytest.fixture(autouse=True)
    def inject_m3(self, mock_pool):
        pool, conn = mock_pool
        alert_engine_m3.inject_deps(pool, None, None)
        return pool, conn

    def _make_rows(self, n=22, breakout_idx=21, breakout_vol=4_000_000):
        """Generate daily rows with a breakout at breakout_idx."""
        rows = []
        d = date(2026, 2, 1)
        from app.utils.trading_hours import add_trading_days
        for i in range(n):
            vol = breakout_vol if i == breakout_idx else 900_000
            close = 26000.0 if i == breakout_idx else 25000.0
            rows.append(FakeRow({
                "ticker": "HPG",
                "date": d, "open": 25000.0, "high": 26100.0,
                "low": 24900.0, "close": close, "volume": vol,
            }))
            d = add_trading_days(d, 1)
        return rows

    @pytest.mark.asyncio
    async def test_dry_run_finds_breakout(self, mock_pool):
        """replay_history(apply=False) returns candidate without creating cycle."""
        pool, conn = mock_pool
        rows = self._make_rows()
        conn.fetch = AsyncMock(side_effect=[
            [FakeRow({"ticker": "HPG", "eligible_for_m3": True, "game_type": "institutional"})],  # watchlist meta
            rows,       # daily_ohlcv
            [],         # existing cycle_events
        ])

        with patch.object(settings, "WATCHLIST", ["HPG"]), \
             patch("app.services.alert_engine_m3.universe_service.get_active_tickers", new=AsyncMock(return_value=["HPG"])):
            result = await alert_engine_m3.replay_history(days=25, apply=False)

        results = result["candidates"]
        assert len(results) >= 1
        assert results[0]["ticker"] == "HPG"
        assert results[0]["is_new"] is True
        assert results[0]["created"] is False   # dry-run: not created

    @pytest.mark.asyncio
    async def test_apply_creates_cycle(self, mock_pool):
        """replay_history(apply=True) creates cycle_event without notifications."""
        pool, conn = mock_pool
        rows = self._make_rows()
        conn.fetch = AsyncMock(side_effect=[
            [FakeRow({"ticker": "HPG", "eligible_for_m3": True, "game_type": "institutional"})],  # watchlist meta
            rows,   # daily_ohlcv
            [],     # existing cycles
        ])
        conn.fetchrow = AsyncMock(side_effect=[
            None,   # source_alert lookup (no alert found)
        ])
        conn.fetchval = AsyncMock(return_value=99)  # INSERT RETURNING id

        with patch.object(settings, "WATCHLIST", ["HPG"]), \
             patch("app.services.alert_engine_m3.universe_service.get_active_tickers", new=AsyncMock(return_value=["HPG"])), \
             patch("app.services.alert_engine_m3.notification") as mock_notif:
            mock_notif.send_cycle_breakout_email = AsyncMock()
            mock_notif.send_m3_replay_digest = AsyncMock()
            result = await alert_engine_m3.replay_history(days=25, apply=True)

        assert any(r["created"] for r in result["candidates"])
        # notify=False → email must NOT be sent per-cycle
        mock_notif.send_cycle_breakout_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotent_skips_existing_cycles(self, mock_pool):
        """Existing cycle for same (ticker, breakout_date) is not re-created."""
        pool, conn = mock_pool
        rows = self._make_rows()
        # Return existing cycle for the breakout date
        breakout_date = rows[-1]["date"]
        conn.fetch = AsyncMock(side_effect=[
            [FakeRow({"ticker": "HPG", "eligible_for_m3": True, "game_type": "institutional"})],  # watchlist meta
            rows,
            [FakeRow({"ticker": "HPG", "breakout_date": breakout_date})],
        ])

        with patch.object(settings, "WATCHLIST", ["HPG"]), \
             patch("app.services.alert_engine_m3.universe_service.get_active_tickers", new=AsyncMock(return_value=["HPG"])):
            result = await alert_engine_m3.replay_history(days=25, apply=True)

        matching = [r for r in result["candidates"] if r["ticker"] == "HPG"]
        assert all(not r["created"] for r in matching)


class TestRunDailyDigest:
    @pytest.fixture(autouse=True)
    def inject_m3(self, mock_pool):
        pool, _ = mock_pool
        alert_engine_m3.inject_deps(pool, None, None)

    @pytest.mark.asyncio
    async def test_run_daily_sends_digest_when_events_exist(self):
        summary_a = {
            "breakouts": [{"ticker": "AAA", "vol_ratio": 4.2, "price_change_pct": 5.1, "game_type": "institutional"}],
            "ten_day_warnings": [],
            "bottoming_candidates": [],
            "invalidations": [],
        }
        summary_b = {
            "breakouts": [],
            "ten_day_warnings": [{"ticker": "NVL", "days_remaining": 8}],
            "bottoming_candidates": [{"ticker": "PDR", "trading_days_elapsed": 20}],
            "invalidations": [],
        }

        with patch("app.services.alert_engine_m3.universe_service.get_active_tickers", new=AsyncMock(return_value=["AAA", "NVL"])), \
             patch("app.services.alert_engine_m3._analyze_ticker", new=AsyncMock(side_effect=[summary_a, summary_b])), \
             patch("app.services.alert_engine_m3.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.is_trading_day", return_value=True):
            mock_notif.send_m3_daily_digest = AsyncMock()
            await alert_engine_m3.run_daily()

        mock_notif.send_m3_daily_digest.assert_awaited_once()
        sent_summary = mock_notif.send_m3_daily_digest.call_args[0][1]
        assert len(sent_summary["breakouts"]) == 1
        assert len(sent_summary["ten_day_warnings"]) == 1
        assert len(sent_summary["bottoming_candidates"]) == 1

    @pytest.mark.asyncio
    async def test_run_daily_skips_digest_when_no_events(self):
        empty = {
            "breakouts": [],
            "ten_day_warnings": [],
            "bottoming_candidates": [],
            "invalidations": [],
        }

        with patch("app.services.alert_engine_m3.universe_service.get_active_tickers", new=AsyncMock(return_value=["AAA"])), \
             patch("app.services.alert_engine_m3._analyze_ticker", new=AsyncMock(return_value=empty)), \
             patch("app.services.alert_engine_m3.notification") as mock_notif, \
             patch("app.services.alert_engine_m3.is_trading_day", return_value=True):
            mock_notif.send_m3_daily_digest = AsyncMock()
            await alert_engine_m3.run_daily()

        mock_notif.send_m3_daily_digest.assert_not_called()
