"""Admin endpoints — read-only diagnostic tools."""
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.database import get_db

router = APIRouter()


@router.get("/scan-history")
async def scan_history(
    days: int = Query(default=25, ge=1, le=60),
    pool: asyncpg.Pool = Depends(get_db),
):
    """
    Retrospective M3 scan: tìm breakout candidates trong N ngày qua từ daily_ohlcv.
    READ-ONLY — không tạo cycle, chỉ report.
    """
    cutoff = date.today() - timedelta(days=days)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ticker, date, open, high, low, close, volume
            FROM daily_ohlcv
            WHERE ticker = ANY($1)
              AND date >= $2
            ORDER BY ticker, date ASC
            """,
            settings.WATCHLIST,
            cutoff,
        )
        existing = await conn.fetch(
            """
            SELECT ticker, breakout_date, id, phase
            FROM cycle_events
            WHERE breakout_date >= $1
            """,
            cutoff,
        )

    existing_map = {
        (r["ticker"], r["breakout_date"]): {"id": r["id"], "phase": r["phase"]}
        for r in existing
    }

    by_ticker: dict[str, list] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(dict(r))

    candidates = []
    for ticker in settings.WATCHLIST:
        trows = by_ticker.get(ticker, [])
        if len(trows) < 2:
            continue
        for i in range(1, len(trows)):
            today_row = trows[i]
            prev_row = trows[i - 1]
            if not today_row["volume"] or not prev_row["close"] or not today_row["close"]:
                continue
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
            "tickers_with_data": len(by_ticker),
            "tickers_no_data": [t for t in settings.WATCHLIST if t not in by_ticker],
            "days_scanned": days,
            "thresholds": {
                "vol_mult": settings.BREAKOUT_VOL_MULT,
                "price_pct": settings.BREAKOUT_PRICE_PCT,
            },
        },
    }
