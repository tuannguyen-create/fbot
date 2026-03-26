"""Admin endpoints — diagnostic and backfill tools."""
import logging
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Auth guard ─────────────────────────────────────────────────────────────

async def _require_admin_key(x_admin_key: Optional[str] = Header(None)):
    """Dependency: validate X-Admin-Key header.

    - ADMIN_API_KEY unset + IS_DEV=True  → open (local dev)
    - ADMIN_API_KEY unset + IS_DEV=False → open but warns (misconfigured prod)
    - ADMIN_API_KEY set                  → header must match exactly
    """
    if not settings.ADMIN_API_KEY:
        if not settings.IS_DEV:
            logger.warning(
                "ADMIN_API_KEY is not set — /admin endpoints are open. "
                "Set ADMIN_API_KEY in production to restrict access."
            )
        return
    if x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── M3 daily scan (read-only) ──────────────────────────────────────────────

@router.get("/scan-history")
async def scan_history(
    days: int = Query(default=25, ge=1, le=60),
    pool: asyncpg.Pool = Depends(get_db),
    _: None = Depends(_require_admin_key),
):
    """Retrospective M3 scan: find breakout candidates from daily_ohlcv.
    Loads extra lookback for MA20 accuracy but only returns candidates within
    the requested days window. READ-ONLY — no cycle_events created.
    """
    scan_start = date.today() - timedelta(days=days)
    cutoff = date.today() - timedelta(days=days + 15)  # extra calendar buffer for MA20

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ticker, date, open, high, low, close, volume
            FROM daily_ohlcv
            WHERE ticker = ANY($1) AND date >= $2
            ORDER BY ticker, date ASC
            """,
            settings.WATCHLIST,
            cutoff,
        )
        existing = await conn.fetch(
            """
            SELECT ticker, breakout_date, id, phase
            FROM cycle_events WHERE breakout_date >= $1
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
            prev_row  = trows[i - 1]
            # Skip lookback rows — only report within the requested window
            if today_row["date"] < scan_start:
                continue
            if not today_row["volume"] or not prev_row["close"] or not today_row["close"]:
                continue
            hist_vols = [r["volume"] for r in trows[:i] if r["volume"]]
            if len(hist_vols) < 3:
                continue
            ma20 = mean(hist_vols[-20:]) if len(hist_vols) >= 20 else mean(hist_vols)
            if not ma20:
                continue
            vol_ratio = today_row["volume"] / ma20
            price_chg = (today_row["close"] - prev_row["close"]) / prev_row["close"]
            if vol_ratio >= settings.BREAKOUT_VOL_MULT and price_chg >= settings.BREAKOUT_PRICE_PCT:
                bd = today_row["date"]
                ec = existing_map.get((ticker, bd))
                candidates.append({
                    "ticker": ticker,
                    "breakout_date": str(bd),
                    "vol_ratio": round(vol_ratio, 2),
                    "price_change_pct": round(price_chg * 100, 2),
                    "volume": today_row["volume"],
                    "close": today_row["close"],
                    "ma20_used": int(ma20),
                    "cycle_id": ec["id"] if ec else None,
                    "cycle_phase": ec["phase"] if ec else None,
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


# ── M1 historical scan (read-only) ─────────────────────────────────────────

@router.get("/scan-m1-history")
async def scan_m1_history(
    days: int = Query(default=25, ge=1, le=60),
    _: None = Depends(_require_admin_key),
):
    """Scan intraday_1m for bars that would trigger M1 alerts.
    READ-ONLY — uses evaluate_bar(), no volume_alerts created.
    Requires intraday_1m to be populated (backfill_intraday on startup).
    """
    from app.services import alert_engine_m1
    hits = await alert_engine_m1.scan_m1_history(days=days)
    return {
        "success": True,
        "data": {
            "hits": hits,
            "total": len(hits),
            "days_scanned": days,
            "thresholds": {
                "normal": settings.THRESHOLD_NORMAL,
                "magic": settings.THRESHOLD_MAGIC,
            },
        },
    }


# ── M3 historical replay ───────────────────────────────────────────────────

@router.post("/replay-m3-history")
async def replay_m3_history(
    days: int = Query(default=25, ge=1, le=60),
    apply: bool = Query(default=False),
    _: None = Depends(_require_admin_key),
):
    """Replay M3 breakout detection over historical daily_ohlcv.

    apply=false (default): dry-run, returns candidates — no DB writes.
    apply=true: creates cycle_events for new breakouts (no email/SSE).

    Idempotent: existing (ticker, breakout_date) cycles are never re-created.
    """
    from app.services import alert_engine_m3
    results = await alert_engine_m3.replay_history(days=days, apply=apply)
    return {
        "success": True,
        "data": {
            "candidates": results,
            "total": len(results),
            "new_found": sum(1 for r in results if r["is_new"]),
            "created": sum(1 for r in results if r.get("created")),
            "days_scanned": days,
            "applied": apply,
            "thresholds": {
                "vol_mult": settings.BREAKOUT_VOL_MULT,
                "price_pct": settings.BREAKOUT_PRICE_PCT,
            },
        },
    }
