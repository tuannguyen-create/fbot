"""App Settings API."""
import logging
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
import asyncpg

from app.config import settings as app_settings
from app.database import get_db
from app.services import stream_ingester

logger = logging.getLogger(__name__)
router = APIRouter()


class ThresholdUpdate(BaseModel):
    threshold_normal: Optional[float] = None
    threshold_magic: Optional[float] = None
    threshold_confirm_15m: Optional[float] = None


async def _get_db_settings(conn) -> dict:
    rows = await conn.fetch("SELECT key, value FROM app_settings")
    return {r["key"]: r["value"] for r in rows}


@router.get("")
async def get_settings(pool: asyncpg.Pool = Depends(get_db)):
    async with pool.acquire() as conn:
        db_cfg = await _get_db_settings(conn)

    return {
        "success": True,
        "data": {
            "threshold_normal": float(db_cfg.get("threshold_normal", app_settings.THRESHOLD_NORMAL)),
            "threshold_magic": float(db_cfg.get("threshold_magic", app_settings.THRESHOLD_MAGIC)),
            "threshold_confirm_15m": float(db_cfg.get("threshold_confirm_15m", app_settings.THRESHOLD_CONFIRM_15M)),
            "breakout_vol_mult": float(db_cfg.get("breakout_vol_mult", app_settings.BREAKOUT_VOL_MULT)),
            "breakout_price_pct": float(db_cfg.get("breakout_price_pct", app_settings.BREAKOUT_PRICE_PCT)),
            "alert_days_before_cycle": int(db_cfg.get("alert_days_before_cycle", app_settings.ALERT_DAYS_BEFORE_CYCLE)),
            "watchlist_count": len(app_settings.WATCHLIST),
            "stream_status": stream_ingester.get_status(),
        },
    }


@router.put("/thresholds")
async def update_thresholds(body: ThresholdUpdate, pool: asyncpg.Pool = Depends(get_db)):
    updates = {}
    if body.threshold_normal is not None:
        updates["threshold_normal"] = str(body.threshold_normal)
    if body.threshold_magic is not None:
        updates["threshold_magic"] = str(body.threshold_magic)
    if body.threshold_confirm_15m is not None:
        updates["threshold_confirm_15m"] = str(body.threshold_confirm_15m)

    if updates:
        async with pool.acquire() as conn:
            for key, value in updates.items():
                await conn.execute(
                    """
                    INSERT INTO app_settings (key, value, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=NOW()
                    """,
                    key,
                    value,
                )
        # Update in-memory settings
        if "threshold_normal" in updates:
            app_settings.THRESHOLD_NORMAL = float(updates["threshold_normal"])
        if "threshold_magic" in updates:
            app_settings.THRESHOLD_MAGIC = float(updates["threshold_magic"])
        if "threshold_confirm_15m" in updates:
            app_settings.THRESHOLD_CONFIRM_15M = float(updates["threshold_confirm_15m"])

    return {"success": True, "data": {"updated": True}}
