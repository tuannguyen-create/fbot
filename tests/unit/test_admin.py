"""Unit tests for admin scan-history endpoint."""
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.api.admin import _require_admin_key, scan_history


class FakeRow(dict):
    """Dict that supports both row['key'] and row.key access (matches asyncpg Record)."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


# ──────────────────────────────────────────────
# Auth guard
# ──────────────────────────────────────────────

class TestAuthGuard:
    @pytest.mark.asyncio
    async def test_no_key_configured_allows_through(self):
        """ADMIN_API_KEY empty → no auth required (dev default)."""
        with patch("app.api.admin.settings") as mock_settings:
            mock_settings.ADMIN_API_KEY = ""
            mock_settings.IS_DEV = True
            # Should not raise
            await _require_admin_key(x_admin_key=None)

    @pytest.mark.asyncio
    async def test_correct_key_allows_through(self):
        """Correct X-Admin-Key header → allowed."""
        with patch("app.api.admin.settings") as mock_settings:
            mock_settings.ADMIN_API_KEY = "secret123"
            mock_settings.IS_DEV = False
            await _require_admin_key(x_admin_key="secret123")

    @pytest.mark.asyncio
    async def test_wrong_key_raises_401(self):
        """Wrong X-Admin-Key header → 401 Unauthorized."""
        with patch("app.api.admin.settings") as mock_settings:
            mock_settings.ADMIN_API_KEY = "secret123"
            mock_settings.IS_DEV = False
            with pytest.raises(HTTPException) as exc_info:
                await _require_admin_key(x_admin_key="wrong")
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_key_when_required_raises_401(self):
        """No header when ADMIN_API_KEY set → 401."""
        with patch("app.api.admin.settings") as mock_settings:
            mock_settings.ADMIN_API_KEY = "secret123"
            mock_settings.IS_DEV = False
            with pytest.raises(HTTPException) as exc_info:
                await _require_admin_key(x_admin_key=None)
            assert exc_info.value.status_code == 401


# ──────────────────────────────────────────────
# scan_history — eligibility filter (Fix 1 / Medium)
# ──────────────────────────────────────────────

class TestScanHistoryEligibility:
    @pytest.mark.asyncio
    async def test_no_eligible_tickers_returns_empty_not_watchlist(self, mock_pool):
        """When DB returns no eligible tickers, return empty — do NOT fall back to full WATCHLIST."""
        pool, conn = mock_pool
        scan_start = date(2026, 1, 6)  # Monday

        conn.fetch = AsyncMock(side_effect=[
            [],   # watchlist query → no eligible tickers
            [],   # cycle_events query
        ])

        with patch("app.api.admin.get_prev_trading_days", return_value=[scan_start]):
            result = await scan_history(days=5, pool=pool)

        data = result["data"]
        assert data["total"] == 0
        assert data["tickers_scanned"] == 0
        assert data["breakout_candidates"] == []
        assert "note" in data
        assert "eligible_for_m3" in data["note"]

    @pytest.mark.asyncio
    async def test_only_eligible_tickers_scanned(self, mock_pool):
        """Only tickers with eligible_for_m3=TRUE are scanned, not the full WATCHLIST."""
        pool, conn = mock_pool
        scan_start = date(2026, 1, 6)

        # DB returns only HPG as eligible (not all 33 WATCHLIST tickers)
        conn.fetch = AsyncMock(side_effect=[
            [FakeRow({"ticker": "HPG"})],   # watchlist
            [],                              # cycle_events
        ])

        mock_bars = []  # no bars → no candidates

        with patch("app.api.admin.get_prev_trading_days", return_value=[scan_start]), \
             patch("app.api.admin._fetch_historical_blocking", return_value=mock_bars):
            result = await scan_history(days=5, pool=pool)

        assert result["data"]["tickers_scanned"] == 1  # only HPG, not 33


# ──────────────────────────────────────────────
# scan_history — breakout detection (Fix 3 / correctness)
# ──────────────────────────────────────────────

def _make_bars(ticker, lookback_n, lookback_vol, scan_dates, scan_close_pct):
    """Build a bar list: N lookback bars + scan bars with a price spike on the last one.

    lookback_vol: normal volume used for lookback period
    scan_dates: list of date objects in the scan range
    scan_close_pct: price change % on the last scan bar (e.g. 0.05 = +5%)
    """
    bars = []
    base_close = 25000.0

    # Lookback bars (before scan range) — all volume=lookback_vol, flat price
    lookback_start = date(2025, 12, 1)
    from app.utils.trading_hours import add_trading_days
    d = lookback_start
    for _ in range(lookback_n):
        bars.append({"ticker": ticker, "date": d, "close": base_close,
                     "volume": lookback_vol, "open": base_close, "high": base_close, "low": base_close})
        d = add_trading_days(d, 1)

    # Scan range bars: normal until last one
    for i, sd in enumerate(scan_dates):
        is_last = i == len(scan_dates) - 1
        if is_last:
            close = base_close * (1 + scan_close_pct)
            volume = int(lookback_vol * 5)  # 5× spike
        else:
            close = base_close
            volume = lookback_vol
        bars.append({"ticker": ticker, "date": sd, "close": close,
                     "volume": volume, "open": base_close, "high": close, "low": base_close})

    return bars


class TestBreakoutDetection:
    @pytest.mark.asyncio
    async def test_breakout_candidate_detected(self, mock_pool):
        """5× volume + 5% price change → breakout candidate reported."""
        from app.config import settings
        pool, conn = mock_pool

        scan_dates = [date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)]

        conn.fetch = AsyncMock(side_effect=[
            [FakeRow({"ticker": "HPG"})],  # watchlist
            [],                             # cycle_events — no existing cycle
        ])

        bars = _make_bars("HPG", lookback_n=22, lookback_vol=100_000,
                          scan_dates=scan_dates, scan_close_pct=0.05)

        with patch("app.api.admin.get_prev_trading_days", return_value=scan_dates), \
             patch("app.api.admin.is_trading_day", return_value=True), \
             patch("app.api.admin._fetch_historical_blocking", return_value=bars):
            result = await scan_history(days=3, pool=pool)

        candidates = result["data"]["breakout_candidates"]
        assert len(candidates) >= 1

        last = candidates[0]
        assert last["ticker"] == "HPG"
        assert last["vol_ratio"] >= settings.BREAKOUT_VOL_MULT
        assert last["price_change_pct"] >= settings.BREAKOUT_PRICE_PCT * 100
        assert last["cycle_id"] is None
        assert last["cycle_phase"] is None

    @pytest.mark.asyncio
    async def test_no_breakout_below_threshold(self, mock_pool):
        """2× volume + 1% price → below thresholds → no candidate."""
        pool, conn = mock_pool
        scan_dates = [date(2026, 1, 6), date(2026, 1, 7)]

        conn.fetch = AsyncMock(side_effect=[
            [FakeRow({"ticker": "HPG"})],
            [],
        ])

        bars = _make_bars("HPG", lookback_n=22, lookback_vol=100_000,
                          scan_dates=scan_dates, scan_close_pct=0.01)
        # Override last bar volume to 2× (below 3× threshold)
        bars[-1]["volume"] = 200_000

        with patch("app.api.admin.get_prev_trading_days", return_value=scan_dates), \
             patch("app.api.admin.is_trading_day", return_value=True), \
             patch("app.api.admin._fetch_historical_blocking", return_value=bars):
            result = await scan_history(days=2, pool=pool)

        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_existing_cycle_linked_in_candidate(self, mock_pool):
        """If a cycle_event exists for the breakout date, cycle_id/phase are populated."""
        pool, conn = mock_pool
        scan_dates = [date(2026, 1, 6), date(2026, 1, 7)]

        conn.fetch = AsyncMock(side_effect=[
            [FakeRow({"ticker": "HPG"})],
            [FakeRow({"ticker": "HPG", "breakout_date": scan_dates[-1], "id": 42, "phase": "distribution"})],
        ])

        bars = _make_bars("HPG", lookback_n=22, lookback_vol=100_000,
                          scan_dates=scan_dates, scan_close_pct=0.05)

        with patch("app.api.admin.get_prev_trading_days", return_value=scan_dates), \
             patch("app.api.admin.is_trading_day", return_value=True), \
             patch("app.api.admin._fetch_historical_blocking", return_value=bars):
            result = await scan_history(days=2, pool=pool)

        candidates = result["data"]["breakout_candidates"]
        assert len(candidates) >= 1
        assert candidates[0]["cycle_id"] == 42
        assert candidates[0]["cycle_phase"] == "distribution"
