"""Cycle Events API endpoints."""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
import asyncpg

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_cycles(
    phase: Optional[str] = None,
    ticker: Optional[str] = None,
    limit: int = Query(default=20, le=100),
    offset: int = 0,
    pool: asyncpg.Pool = Depends(get_db),
):
    conditions = []
    params = []
    idx = 1

    if phase:
        # Support comma-separated phases e.g. "distributing,bottoming"
        phases = [p.strip() for p in phase.split(",")]
        placeholders = ", ".join(f"${i}" for i in range(idx, idx + len(phases)))
        conditions.append(f"phase IN ({placeholders})")
        params.extend(phases)
        idx += len(phases)
    if ticker:
        conditions.append(f"ticker = ${idx}")
        params.append(ticker.upper())
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM cycle_events {where}", *params)
        rows = await conn.fetch(
            f"""
            SELECT id, ticker, breakout_date, phase, days_remaining,
                   predicted_bottom_date, trading_days_elapsed, estimated_dist_days,
                   peak_volume, breakout_price, alert_sent_10d, alert_sent_bottom,
                   created_at, updated_at
            FROM cycle_events {where}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx+1}
            """,
            *params,
            limit,
            offset,
        )

    cycles = [dict(r) for r in rows]
    return {"success": True, "data": {"cycles": cycles, "total": total}}


@router.get("/{cycle_id}")
async def get_cycle(cycle_id: int, pool: asyncpg.Pool = Depends(get_db)):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, ticker, breakout_date, phase, days_remaining,
                   predicted_bottom_date, trading_days_elapsed, estimated_dist_days,
                   peak_volume, breakout_price, alert_sent_10d, alert_sent_bottom,
                   created_at, updated_at
            FROM cycle_events WHERE id=$1
            """,
            cycle_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail=f"Cycle #{cycle_id} not found")
    return {"success": True, "data": {"cycle": dict(row)}}
