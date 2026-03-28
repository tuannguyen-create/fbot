"""Admin endpoints — diagnostic and backfill tools."""
import logging
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from app.config import settings
from app.database import get_db
from app.services import universe_service

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
    tickers = await universe_service.get_active_tickers()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ticker, date, open, high, low, close, volume
            FROM daily_ohlcv
            WHERE ticker = ANY($1) AND date >= $2
            ORDER BY ticker, date ASC
            """,
            tickers,
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
    for ticker in tickers:
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
            "tickers_no_data": [t for t in tickers if t not in by_ticker],
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


# ── M1 historical replay ───────────────────────────────────────────────────

@router.post("/replay-m1-history")
async def replay_m1_history(
    days: int = Query(default=25, ge=1, le=60),
    apply: bool = Query(default=False),
    mode: str = Query(default="bootstrap", pattern="^(bootstrap|recovery|manual)$"),
    notify_mode: str = Query(default="none", pattern="^(none|digest)$"),
    _: None = Depends(_require_admin_key),
):
    """Replay historical M1 alerts: detect + optionally persist + optionally digest.

    apply=false (default): dry-run, returns hits — no DB writes.
    apply=true: inserts volume_alerts with origin='historical_replay', is_actionable=FALSE.

    Idempotent: existing alert at same ticker+slot+ICT-date is skipped.

    APPROXIMATION NOTICE: bar-close volume only, not intra-minute tick detection.
    Use as research/audit/recovery layer.
    """
    from app.services import alert_engine_m1
    result = await alert_engine_m1.replay_m1_history(
        days=days, apply=apply, mode=mode, notify_mode=notify_mode,
    )
    return {"success": True, "data": result}


# ── M3 historical replay ───────────────────────────────────────────────────

@router.post("/replay-m3-history")
async def replay_m3_history(
    days: int = Query(default=25, ge=1, le=60),
    apply: bool = Query(default=False),
    mode: str = Query(default="bootstrap", pattern="^(bootstrap|recovery|manual)$"),
    notify_mode: str = Query(default="none", pattern="^(none|digest)$"),
    _: None = Depends(_require_admin_key),
):
    """Replay M3 breakout detection over historical daily_ohlcv.

    apply=false (default): dry-run, returns candidates — no DB writes.
    apply=true: creates cycle_events with origin='historical_replay' (no per-cycle email/SSE).

    Idempotent: existing (ticker, breakout_date) cycles are never re-created.
    """
    from app.services import alert_engine_m3
    result = await alert_engine_m3.replay_history(
        days=days, apply=apply, mode=mode, notify_mode=notify_mode,
    )
    candidates = result["candidates"]
    return {
        "success": True,
        "data": {
            "candidates": candidates,
            "total": len(candidates),
            "new_found": sum(1 for r in candidates if r["is_new"]),
            "created": result["created_count"],
            "run_id": result["run_id"],
            "days_scanned": days,
            "applied": apply,
            "mode": mode,
            "notify_mode": notify_mode,
            "thresholds": {
                "vol_mult": settings.BREAKOUT_VOL_MULT,
                "price_pct": settings.BREAKOUT_PRICE_PCT,
            },
        },
    }


# ── Replay runs audit ──────────────────────────────────────────────────────

@router.get("/replay-runs")
async def list_replay_runs(
    module: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    pool: asyncpg.Pool = Depends(get_db),
    _: None = Depends(_require_admin_key),
):
    """List past replay/backfill runs for audit."""
    where = "WHERE module = $1" if module else ""
    params = [module] if module else []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, module, mode, date_from, date_to, apply, notify_mode,
                   created_count, skipped_count, status, started_at, finished_at, error
            FROM replay_runs
            {where}
            ORDER BY started_at DESC
            LIMIT {limit}
            """,
            *params,
        )
    return {"success": True, "data": {"runs": [dict(r) for r in rows]}}


@router.get("/replay-runs/{run_id}")
async def get_replay_run(
    run_id: str,
    pool: asyncpg.Pool = Depends(get_db),
    _: None = Depends(_require_admin_key),
):
    """Get details of a specific replay run by UUID."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, module, mode, date_from, date_to, apply, notify_mode,
                   created_count, skipped_count, status, started_at, finished_at, error
            FROM replay_runs WHERE id = $1::uuid
            """,
            run_id,
        )
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Replay run {run_id} not found")
    return {"success": True, "data": {"run": dict(row)}}


@router.post("/cleanup-stuck-runs")
async def cleanup_stuck_runs(
    pool: asyncpg.Pool = Depends(get_db),
    _: None = Depends(_require_admin_key),
):
    """Mark stuck 'running' replay runs as 'failed' (no finished_at after 10 min)."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE replay_runs
            SET status = 'failed', finished_at = NOW(),
                error = 'Cleaned up: stuck in running state'
            WHERE status = 'running'
              AND started_at < NOW() - INTERVAL '10 minutes'
            """
        )
    count = int(result.split()[-1]) if result else 0
    return {"success": True, "data": {"cleaned_up": count}}


# ── Watchlist management ──────────────────────────────────────────────────

class SyncWatchlistRequest(BaseModel):
    tickers: list[str]
    vn30: list[str] = []
    exchange: str = "HOSE"
    deactivate_unlisted: bool = False


@router.post("/sync-watchlist")
async def sync_watchlist(
    body: SyncWatchlistRequest,
    pool: asyncpg.Pool = Depends(get_db),
    _: None = Depends(_require_admin_key),
):
    """Bulk upsert tickers into watchlist.

    - New tickers: inserted with active=TRUE, eligible_for_m3=TRUE
    - Existing tickers: activated, in_vn30 updated; company_name/sector/game_type preserved
    - deactivate_unlisted=true: deactivates tickers NOT in the provided list
    """
    tickers = sorted({t.upper().strip() for t in body.tickers if t.strip()})
    if not tickers:
        raise HTTPException(status_code=400, detail="tickers list is empty")

    vn30_set = {t.upper().strip() for t in body.vn30}

    async with pool.acquire() as conn:
        rows = [
            (t, body.exchange, t in vn30_set, True, True)
            for t in tickers
        ]
        await conn.executemany(
            """
            INSERT INTO watchlist (ticker, exchange, in_vn30, active, eligible_for_m3)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (ticker) DO UPDATE SET
                in_vn30 = EXCLUDED.in_vn30,
                active = TRUE,
                eligible_for_m3 = TRUE
            """,
            rows,
        )

        deactivated = 0
        if body.deactivate_unlisted:
            result = await conn.execute(
                "UPDATE watchlist SET active = FALSE "
                "WHERE NOT (ticker = ANY($1::text[])) AND active = TRUE",
                tickers,
            )
            deactivated = int(result.split()[-1]) if result else 0

        total_active = await conn.fetchval(
            "SELECT COUNT(*) FROM watchlist WHERE active = TRUE"
        )
        total_vn30 = await conn.fetchval(
            "SELECT COUNT(*) FROM watchlist WHERE in_vn30 = TRUE AND active = TRUE"
        )

    logger.info(
        f"Watchlist sync: {len(tickers)} upserted, {deactivated} deactivated, "
        f"{total_active} total active, {total_vn30} VN30"
    )

    return {
        "success": True,
        "data": {
            "upserted": len(tickers),
            "deactivated": deactivated,
            "total_active": total_active,
            "total_vn30": total_vn30,
        },
    }


@router.post("/backfill-daily")
async def admin_backfill_daily(
    days: int = Query(default=25, ge=1, le=60),
    _: None = Depends(_require_admin_key),
):
    """Manually trigger daily OHLCV backfill from FiinQuantX.

    Required after expanding watchlist — new tickers have no daily_ohlcv data.
    Capped to FIINQUANT_TICKER_LIMIT tickers (VN30 first).
    """
    from app.services import daily_ohlcv_service
    await daily_ohlcv_service.backfill_historical(days=days)
    active = await universe_service.get_active_tickers()
    effective = min(len(active), settings.FIINQUANT_TICKER_LIMIT)
    return {
        "success": True,
        "data": {
            "days": days,
            "ticker_limit": settings.FIINQUANT_TICKER_LIMIT,
            "active_tickers": len(active),
            "effective_tickers": effective,
        },
    }


@router.post("/backfill-intraday")
async def admin_backfill_intraday(
    days: Optional[int] = Query(default=None, ge=1, le=180),
    _: None = Depends(_require_admin_key),
):
    """Manually trigger intraday 1m backfill within plan retention window.

    days=null: use FIINQUANT_INTRADAY_HISTORY_DAYS (plan-aware default).
    Returns number of bars fetched and upserted.
    """
    from app.services import historical_intraday_service
    total = await historical_intraday_service.backfill_intraday(days=days)
    return {
        "success": True,
        "data": {
            "bars_upserted": total,
            "retention_days": settings.FIINQUANT_INTRADAY_HISTORY_DAYS,
            "ticker_limit": settings.FIINQUANT_TICKER_LIMIT,
        },
    }
