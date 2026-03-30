"""Alert Feed API endpoints."""
import logging
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
import asyncpg

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


def _row_to_dict(row) -> dict:
    return dict(row)


@router.get("")
async def list_alerts(
    ticker: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    magic_only: bool = False,
    origin: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    pool: asyncpg.Pool = Depends(get_db),
):
    conditions = []
    params = []
    idx = 1

    if ticker:
        conditions.append(f"ticker = ${idx}")
        params.append(ticker.upper())
        idx += 1
    if date_from:
        conditions.append(f"DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh') >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to:
        conditions.append(f"DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh') <= ${idx}")
        params.append(date_to)
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1
    if magic_only:
        conditions.append("in_magic_window = TRUE")
    if origin:
        conditions.append(f"origin = ${idx}")
        params.append(origin)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM volume_alerts {where}", *params)
        rows = await conn.fetch(
            f"""
            SELECT id, ticker, fired_at, bar_time, slot, volume, ratio_5d, bu_pct,
                   in_magic_window, status, quality_grade, quality_score, origin, is_actionable
            FROM volume_alerts {where}
            ORDER BY bar_time DESC
            LIMIT ${idx} OFFSET ${idx+1}
            """,
            *params,
            limit,
            offset,
        )

    alerts = [_row_to_dict(r) for r in rows]
    return {"success": True, "data": {"alerts": alerts, "total": total, "limit": limit, "offset": offset}}


@router.get("/summary/today")
async def today_summary(pool: asyncpg.Pool = Depends(get_db)):
    # Only count live alerts for today's KPIs — historical replays must not inflate numbers
    today_ict = (
        "DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh') = CURRENT_DATE AT TIME ZONE 'Asia/Ho_Chi_Minh'"
        " AND origin = 'live'"
    )
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM volume_alerts WHERE {today_ict}")
        confirmed = await conn.fetchval(f"SELECT COUNT(*) FROM volume_alerts WHERE {today_ict} AND status='confirmed'")
        fired = await conn.fetchval(f"SELECT COUNT(*) FROM volume_alerts WHERE {today_ict} AND status='fired'")
        cancelled = await conn.fetchval(f"SELECT COUNT(*) FROM volume_alerts WHERE {today_ict} AND status='cancelled'")
        ticker_rows = await conn.fetch(
            f"SELECT ticker, COUNT(*) as cnt FROM volume_alerts WHERE {today_ict} GROUP BY ticker"
        )

    by_ticker = {r["ticker"]: r["cnt"] for r in ticker_rows}
    return {
        "success": True,
        "data": {
            "total": total or 0,
            "confirmed": confirmed or 0,
            "fired": fired or 0,
            "cancelled": cancelled or 0,
            "by_ticker": by_ticker,
        },
    }


@router.get("/{alert_id}")
async def get_alert(alert_id: int, pool: asyncpg.Pool = Depends(get_db)):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, ticker, fired_at, bar_time, slot, volume, ratio_5d, bu_pct,
                   in_magic_window, status, baseline_5d, foreign_net,
                   confirmed_at, ratio_15m, email_sent, cycle_event_id,
                   features, quality_score, quality_grade, quality_reason,
                   strong_bull_candle, is_sideways_base,
                   origin, replay_run_id, replayed_at, is_actionable
            FROM volume_alerts WHERE id=$1
            """,
            alert_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail=f"Alert #{alert_id} not found")
    return {"success": True, "data": {"alert": _row_to_dict(row)}}
