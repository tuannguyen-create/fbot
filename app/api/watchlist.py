"""Watchlist API endpoints."""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import asyncpg

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

WATCHLIST_COMPANY_NAMES = {
    "ACB": "Asia Commercial Bank",
    "BCM": "Becamex IDC",
    "BID": "BIDV",
    "CTG": "VietinBank",
    "FPT": "FPT Corporation",
    "GAS": "PV Gas",
    "GVR": "Vietnam Rubber Group",
    "HDB": "HDBank",
    "HPG": "Hoa Phat Group",
    "LPB": "LienVietPostBank",
    "MBB": "MB Bank",
    "MSN": "Masan Group",
    "MWG": "Mobile World",
    "PLX": "Petrolimex",
    "SAB": "Sabeco",
    "SHB": "Saigon-Hanoi Bank",
    "SSB": "SeABank",
    "SSI": "SSI Securities",
    "STB": "Sacombank",
    "TCB": "Techcombank",
    "VCB": "Vietcombank",
    "VHM": "Vinhomes",
    "VIB": "Vietnam International Bank",
    "VIC": "Vingroup",
    "VJC": "Vietjet Air",
    "VNM": "Vinamilk",
    "VPB": "VPBank",
    "VPL": "Vinhomes Project",
    "VRE": "Vincom Retail",
    "VPG": "Viet Phat Group",
    "NVL": "No Va Land",
    "PDR": "Phat Dat Real Estate",
    "KBC": "Kinh Bac City",
}


class TickerM3Settings(BaseModel):
    eligible_for_m3: Optional[bool] = None
    game_type: Optional[str] = None


@router.get("")
async def list_watchlist(pool: asyncpg.Pool = Depends(get_db)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ticker, company_name, exchange, sector, in_vn30, active,
                   eligible_for_m3, game_type
            FROM watchlist
            ORDER BY in_vn30 DESC, ticker
            """
        )
    tickers = [dict(r) for r in rows]
    return {"success": True, "data": {"tickers": tickers}}


@router.patch("/{ticker}/m3")
async def update_ticker_m3_settings(
    ticker: str,
    body: TickerM3Settings,
    pool: asyncpg.Pool = Depends(get_db),
):
    ticker = ticker.upper()
    updates = {}
    if body.eligible_for_m3 is not None:
        updates["eligible_for_m3"] = body.eligible_for_m3
    if body.game_type is not None:
        updates["game_type"] = body.game_type

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clause = ", ".join(f"{k}=${i+2}" for i, k in enumerate(updates))
    values = [ticker] + list(updates.values())

    async with pool.acquire() as conn:
        result = await conn.execute(
            f"UPDATE watchlist SET {set_clause} WHERE ticker=$1",
            *values,
        )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
    return {"success": True, "data": {"ticker": ticker, **updates}}


@router.get("/{ticker}/summary")
async def get_ticker_summary(ticker: str, pool: asyncpg.Pool = Depends(get_db)):
    ticker = ticker.upper()
    async with pool.acquire() as conn:
        # Company name from watchlist
        wl_row = await conn.fetchrow(
            "SELECT company_name FROM watchlist WHERE ticker=$1", ticker
        )
        # Today's alert count
        today_alerts = await conn.fetchval(
            """
            SELECT COUNT(*) FROM volume_alerts
            WHERE ticker=$1
              AND DATE(fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh') = CURRENT_DATE AT TIME ZONE 'Asia/Ho_Chi_Minh'
            """,
            ticker,
        )
        # Active cycle
        cycle_row = await conn.fetchrow(
            """
            SELECT id, ticker, breakout_date, phase, days_remaining,
                   rewatch_window_start, rewatch_window_end,
                   trading_days_elapsed, estimated_dist_days,
                   game_type, phase_reason
            FROM cycle_events
            WHERE ticker=$1
              AND phase IN ('distribution_in_progress', 'bottoming_candidate')
            ORDER BY created_at DESC LIMIT 1
            """,
            ticker,
        )

    company_name = wl_row["company_name"] if wl_row else WATCHLIST_COMPANY_NAMES.get(ticker)
    active_cycle = dict(cycle_row) if cycle_row else None

    return {
        "success": True,
        "data": {
            "ticker": ticker,
            "company_name": company_name,
            "today_alerts": today_alerts or 0,
            "active_cycle": active_cycle,
        },
    }
