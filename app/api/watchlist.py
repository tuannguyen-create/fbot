"""Watchlist API endpoints."""
import logging
from fastapi import APIRouter, Depends
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


@router.get("")
async def list_watchlist(pool: asyncpg.Pool = Depends(get_db)):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ticker, company_name, exchange, sector, in_vn30, active FROM watchlist ORDER BY in_vn30 DESC, ticker"
        )
    tickers = [dict(r) for r in rows]
    return {"success": True, "data": {"tickers": tickers}}


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
                   predicted_bottom_date, trading_days_elapsed, estimated_dist_days
            FROM cycle_events
            WHERE ticker=$1 AND phase IN ('distributing', 'bottoming')
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
