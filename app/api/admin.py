"""Admin endpoints — read-only diagnostic tools."""
import asyncio
from collections import defaultdict
from datetime import date
from statistics import mean
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.config import settings
from app.database import get_db
from app.services.daily_ohlcv_service import _fetch_historical_blocking
from app.utils.trading_hours import get_prev_trading_days, is_trading_day

router = APIRouter()


async def _require_admin_key(x_admin_key: Optional[str] = Header(None)):
    """Auth guard: if ADMIN_API_KEY is set in config, require matching header."""
    if settings.ADMIN_API_KEY and x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/scan-history")
async def scan_history(
    days: int = Query(default=25, ge=1, le=60),
    pool: asyncpg.Pool = Depends(get_db),
    _: None = Depends(_require_admin_key),
):
    """
    Retrospective M3 scan: tìm breakout candidates trong N trading days qua.
    - Gọi FiinQuantX trực tiếp (không đọc DB)
    - Dùng trading days, không phải calendar days
    - Fetch thêm 20 ngày lookback để tính MA20 chính xác
    - Chỉ scan tickers có eligible_for_m3 = TRUE
    READ-ONLY — không tạo cycle, chỉ report.
    """
    # Trading days range: N trading days before today (not including today)
    scan_trading_days = get_prev_trading_days(date.today(), days)
    if not scan_trading_days:
        return {"success": True, "data": {"breakout_candidates": [], "total": 0}}
    scan_start = scan_trading_days[0]

    # M3-eligible tickers only
    async with pool.acquire() as conn:
        wl_rows = await conn.fetch(
            "SELECT ticker FROM watchlist WHERE eligible_for_m3 = TRUE AND active = TRUE"
        )
        existing = await conn.fetch(
            """
            SELECT ticker, breakout_date, id, phase
            FROM cycle_events
            WHERE breakout_date >= $1
            """,
            scan_start,
        )

    eligible_tickers = [r["ticker"] for r in wl_rows] if wl_rows else settings.WATCHLIST

    existing_map = {
        (r["ticker"], r["breakout_date"]): {"id": r["id"], "phase": r["phase"]}
        for r in existing
    }

    # Fetch from FiinQuantX directly: days + 20 lookback, x2 for weekends/holidays
    fetch_period = (days + 20) * 2
    loop = asyncio.get_running_loop()
    bars = await loop.run_in_executor(
        None,
        lambda: _fetch_historical_blocking(eligible_tickers, fetch_period),
    )

    # Group by ticker, sort chronologically
    by_ticker: dict[str, list] = defaultdict(list)
    for b in bars:
        by_ticker[b["ticker"]].append(b)
    for ticker in by_ticker:
        by_ticker[ticker].sort(key=lambda x: x["date"])

    # M3 scan per ticker
    candidates = []
    for ticker in eligible_tickers:
        trows = by_ticker.get(ticker, [])
        if len(trows) < 2:
            continue
        for i in range(1, len(trows)):
            today_row = trows[i]
            # Only analyze rows within scan range (lookback rows excluded)
            if today_row["date"] < scan_start:
                continue
            if not is_trading_day(today_row["date"]):
                continue

            prev_row = trows[i - 1]
            if not today_row["volume"] or not prev_row["close"] or not today_row["close"]:
                continue

            # MA20 uses ALL data before this row (including lookback period)
            hist_vols = [r["volume"] for r in trows[:i] if r["volume"]]
            if len(hist_vols) < 3:
                continue
            ma20 = mean(hist_vols[-20:]) if len(hist_vols) >= 20 else mean(hist_vols)

            vol_ratio = today_row["volume"] / ma20
            price_chg = (today_row["close"] - prev_row["close"]) / prev_row["close"]

            if vol_ratio >= settings.BREAKOUT_VOL_MULT and price_chg >= settings.BREAKOUT_PRICE_PCT:
                bd = today_row["date"]
                existing_cycle = existing_map.get((ticker, bd))
                candidates.append({
                    "ticker": ticker,
                    "breakout_date": str(bd),
                    "vol_ratio": round(vol_ratio, 2),
                    "price_change_pct": round(price_chg * 100, 2),
                    "volume": today_row["volume"],
                    "close": today_row["close"],
                    "ma20_used": int(ma20),
                    "cycle_id": existing_cycle["id"] if existing_cycle else None,
                    "cycle_phase": existing_cycle["phase"] if existing_cycle else None,
                })

    candidates.sort(key=lambda x: x["breakout_date"], reverse=True)

    return {
        "success": True,
        "data": {
            "breakout_candidates": candidates,
            "total": len(candidates),
            "tickers_scanned": len(eligible_tickers),
            "tickers_with_data": len(by_ticker),
            "tickers_no_data": [t for t in eligible_tickers if t not in by_ticker],
            "days_scanned": days,
            "scan_from": str(scan_start),
            "thresholds": {
                "vol_mult": settings.BREAKOUT_VOL_MULT,
                "price_pct": settings.BREAKOUT_PRICE_PCT,
            },
        },
    }
