"""Module 1: Volume Scanner Alert Engine."""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from app.config import settings
from app.utils.timezone import to_ict
from app.utils.trading_hours import get_slot, is_magic_window, is_trading_hours
from app.services import baseline_service, notification

logger = logging.getLogger(__name__)

_pool = None
_redis = None
# SSE broadcast queue — injected from stream module
_alert_queue: Optional[asyncio.Queue] = None


def inject_deps(pool, redis, alert_queue: asyncio.Queue = None):
    global _pool, _redis, _alert_queue
    _pool = pool
    _redis = redis
    if alert_queue is not None:
        _alert_queue = alert_queue


# In-memory: pending 15-min confirmations
# key = ticker, value = {alert_id, slot, confirm_by_slot}
_pending_confirms: dict[str, dict] = {}


async def process(bar: dict):
    """
    Process a single 1-minute bar from FiinQuantX.
    bar = {ticker, bar_time (UTC ISO), open, high, low, close, volume, bu, sd, fb, fs, fn}
    """
    ticker = bar["ticker"]
    try:
        bar_time_utc: datetime = bar["bar_time"]
        if isinstance(bar_time_utc, str):
            bar_time_utc = datetime.fromisoformat(bar_time_utc.replace("Z", "+00:00"))
        bar_time_ict = to_ict(bar_time_utc)

        slot = get_slot(bar_time_ict.time())
        if slot is None:
            return

        baseline = await baseline_service.get_baseline(ticker, slot)
        if baseline is None:
            return
        avg_5d = baseline.get("avg_5d", 0)
        if not avg_5d or avg_5d == 0:
            return

        volume = bar["volume"]
        in_magic = is_magic_window(bar_time_ict.time())
        threshold = settings.THRESHOLD_MAGIC if in_magic else settings.THRESHOLD_NORMAL

        # Rate projection: if FiinQuantX sends mid-minute running updates
        # (bar_time.second > 0), project current volume to full minute.
        # Allows early detection at ~10-20s instead of waiting for bar close.
        elapsed_seconds = bar_time_ict.second
        if elapsed_seconds >= 10:
            projected_volume = int(volume * (60 / elapsed_seconds))
            ratio = projected_volume / avg_5d
        else:
            ratio = volume / avg_5d

        if ratio >= threshold:
            await _fire_alert(ticker, bar, slot, ratio, baseline, in_magic, bar_time_ict)

        # Check pending 15-min confirmations
        await _check_confirmations(ticker, bar, slot)

    except Exception as e:
        logger.error(f"M1 process error for {ticker}: {e}", exc_info=True)


async def _fire_alert(ticker: str, bar: dict, slot: int, ratio: float, baseline: dict, in_magic: bool, bar_time: datetime = None):
    """Insert alert (with dedup) and trigger SSE + email."""
    bu = bar.get("bu", 0) or 0
    sd = bar.get("sd", 0) or 0
    bu_pct: Optional[float] = (bu / (bu + sd) * 100) if (bu + sd) > 0 else None
    foreign_net = bar.get("fn")
    avg_5d = baseline.get("avg_5d")

    # Redis throttle check (30 min) — skip if Redis not configured
    throttle_key = f"alert_throttle:{ticker}:{slot}"
    if _redis is not None and await _redis.exists(throttle_key):
        return

    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO volume_alerts
                    (ticker, slot, bar_time, volume, baseline_5d, ratio_5d, bu_pct, foreign_net,
                     in_magic_window, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'fired')
                ON CONFLICT (ticker, slot, (DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh')))
                DO NOTHING
                RETURNING id, fired_at
                """,
                ticker,
                slot,
                bar_time,
                bar["volume"],
                avg_5d,
                round(ratio, 4),
                round(bu_pct, 4) if bu_pct is not None else None,
                foreign_net,
                in_magic,
            )

        if row is None:
            # Duplicate
            return

        alert_id = row["id"]
        fired_at = row["fired_at"]

        # Set Redis throttle (skip if Redis not configured)
        if _redis is not None:
            await _redis.setex(throttle_key, 1800, "1")

        # Queue for 15-min confirmation
        _pending_confirms[ticker] = {
            "alert_id": alert_id,
            "slot": slot,
            "confirm_by_slot": slot + 15,
            "cumulative_volume": bar["volume"],
        }

        logger.info(f"M1 Alert fired: {ticker} slot={slot} ratio={ratio:.2f}x magic={in_magic}")

        # SSE push
        if _alert_queue is not None:
            await _alert_queue.put({
                "type": "volume_alert",
                "data": {
                    "id": alert_id,
                    "ticker": ticker,
                    "slot": slot,
                    "volume": bar["volume"],
                    "ratio_5d": round(ratio, 2),
                    "bu_pct": round(bu_pct, 1) if bu_pct is not None else None,
                    "in_magic_window": in_magic,
                    "status": "fired",
                    "fired_at": fired_at.isoformat() if fired_at else None,
                },
            })

        # Email notification (async, non-blocking)
        asyncio.create_task(notification.send_volume_alert_email(alert_id))

        # Trigger M3 intraday breakout check (volume spike may = breakout day)
        from app.services import alert_engine_m3
        asyncio.create_task(alert_engine_m3.check_intraday_breakout(ticker, bar, alert_id))

    except Exception as e:
        logger.error(f"M1 fire_alert error for {ticker}: {e}", exc_info=True)


async def _check_confirmations(ticker: str, bar: dict, current_slot: int):
    """Check if any pending 15-min confirm is due."""
    pending = _pending_confirms.get(ticker)
    if not pending:
        return
    if current_slot < pending["confirm_by_slot"]:
        # Accumulate volume
        _pending_confirms[ticker]["cumulative_volume"] += bar["volume"]
        return

    # 15 min elapsed — evaluate
    alert_id = pending["alert_id"]
    orig_slot = pending["slot"]
    cumulative = pending["cumulative_volume"]
    elapsed_slots = current_slot - orig_slot

    baseline = await baseline_service.get_baseline(ticker, orig_slot)
    avg_5d = baseline.get("avg_5d", 0) if baseline else 0
    expected_15m = avg_5d * elapsed_slots if avg_5d else 1
    ratio_15m = cumulative / expected_15m if expected_15m > 0 else 0

    status = "confirmed" if ratio_15m >= settings.THRESHOLD_CONFIRM_15M else "cancelled"

    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE volume_alerts
                SET status=$1, confirmed_at=NOW(), ratio_15m=$2
                WHERE id=$3
                """,
                status,
                round(ratio_15m, 4),
                alert_id,
            )
        logger.info(f"M1 Confirm: {ticker} alert_id={alert_id} status={status} ratio_15m={ratio_15m:.2f}")

        # SSE push — update clients with new status
        if _alert_queue is not None:
            await _alert_queue.put({
                "type": "alert_status_update",
                "data": {
                    "id": alert_id,
                    "status": status,
                    "ratio_15m": round(ratio_15m, 2),
                },
            })
    except Exception as e:
        logger.error(f"M1 confirm error: {e}")

    # Remove from pending
    del _pending_confirms[ticker]
