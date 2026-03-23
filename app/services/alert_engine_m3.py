"""Module 3: Cycle Analysis Alert Engine."""
import asyncio
import logging
from datetime import date, timedelta
from statistics import mean
from typing import Optional

from app.config import settings
from app.utils.trading_hours import is_trading_day, count_trading_days_between, add_trading_days
from app.services import notification

logger = logging.getLogger(__name__)

_pool = None
_redis = None
_alert_queue = None


def inject_deps(pool, redis, alert_queue=None):
    global _pool, _redis, _alert_queue
    _pool = pool
    _redis = redis
    if alert_queue is not None:
        _alert_queue = alert_queue


async def check_intraday_breakout(ticker: str, bar: dict, alert_id: Optional[int] = None):
    """
    Called by M1 when a volume spike fires. Checks if cumulative intraday
    volume already crosses M3 breakout threshold — no need to wait until 15:05.
    """
    # Skip if active distributing cycle already exists
    async with _pool.acquire() as conn:
        active = await conn.fetchrow(
            "SELECT id FROM cycle_events WHERE ticker=$1 AND phase='distributing'",
            ticker,
        )
    if active:
        return

    # Already created a cycle today?
    today = date.today()
    async with _pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT id FROM cycle_events WHERE ticker=$1 AND breakout_date=$2",
            ticker, today,
        )
    if exists:
        return

    # Get MA20 daily volume + yesterday close from daily_ohlcv
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT date, close, volume FROM daily_ohlcv
            WHERE ticker=$1 ORDER BY date DESC LIMIT 21
            """,
            ticker,
        )
    if len(rows) < 3:
        return

    volumes = [r["volume"] for r in rows if r["volume"]]
    ma20 = mean(volumes[:20]) if len(volumes) >= 20 else mean(volumes)
    yesterday_close = rows[0]["close"]
    if not yesterday_close or yesterday_close <= 0 or not ma20:
        return

    # Get today's cumulative intraday volume
    async with _pool.acquire() as conn:
        cum_vol = await conn.fetchval(
            """
            SELECT COALESCE(SUM(volume), 0) FROM intraday_1m
            WHERE ticker=$1
              AND (bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = $2
            """,
            ticker, today,
        )
    if not cum_vol:
        return

    vol_ratio = cum_vol / ma20
    current_price = bar.get("close", 0) or 0
    price_chg = (current_price - yesterday_close) / yesterday_close

    if vol_ratio >= settings.BREAKOUT_VOL_MULT and price_chg >= settings.BREAKOUT_PRICE_PCT:
        logger.info(
            f"M3 Intraday breakout: {ticker} cum_vol={vol_ratio:.1f}x MA20 "
            f"price_chg={price_chg:.1%}"
        )
        fake_row = {"date": today, "volume": cum_vol, "close": current_price}
        await _create_cycle(ticker, fake_row, ma20, alert_id=alert_id)


async def run_daily():
    """
    APScheduler calls this at 15:05 ICT (after 14:30 market close).
    Analyzes daily OHLCV for all tickers.
    """
    if not is_trading_day(date.today()):
        logger.info("M3 daily: skipping (non-trading day)")
        return

    logger.info("M3 daily analysis started")
    for ticker in settings.WATCHLIST:
        try:
            await _analyze_ticker(ticker)
        except Exception as e:
            logger.error(f"M3 analysis error for {ticker}: {e}", exc_info=True)
    logger.info("M3 daily analysis complete")


async def _analyze_ticker(ticker: str):
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT date, open, high, low, close, volume
            FROM daily_ohlcv
            WHERE ticker=$1
            ORDER BY date DESC
            LIMIT 25
            """,
            ticker,
        )

    if len(rows) < 2:
        return

    # rows are newest-first, reverse for chronological
    rows = list(reversed(rows))
    today_row = rows[-1]
    prev_row = rows[-2]

    volumes = [r["volume"] for r in rows if r["volume"]]
    if len(volumes) < 3:
        return

    ma20 = mean(volumes[-20:]) if len(volumes) >= 20 else mean(volumes)

    # --- Breakout detection ---
    vol_ratio = today_row["volume"] / ma20 if ma20 > 0 else 0
    price_chg = (
        (today_row["close"] - prev_row["close"]) / prev_row["close"]
        if prev_row["close"] > 0
        else 0
    )

    if vol_ratio >= settings.BREAKOUT_VOL_MULT and price_chg >= settings.BREAKOUT_PRICE_PCT:
        async with _pool.acquire() as conn:
            active = await conn.fetchrow(
                "SELECT id FROM cycle_events WHERE ticker=$1 AND phase='distributing'",
                ticker,
            )
        if not active:
            await _create_cycle(ticker, today_row, ma20)
            return

    # --- Update existing cycles ---
    async with _pool.acquire() as conn:
        cycles = await conn.fetch(
            "SELECT * FROM cycle_events WHERE ticker=$1 AND phase IN ('distributing', 'bottoming')",
            ticker,
        )
    for cycle in cycles:
        await _update_cycle(ticker, dict(cycle), rows, ma20)


async def _create_cycle(ticker: str, today_row, ma20: float, alert_id: Optional[int] = None):
    est_dist_days = 20
    breakout_date = today_row["date"]
    predicted_bottom = add_trading_days(breakout_date, est_dist_days)

    async with _pool.acquire() as conn:
        cycle_id = await conn.fetchval(
            """
            INSERT INTO cycle_events
                (ticker, breakout_date, peak_volume, breakout_price,
                 estimated_dist_days, days_remaining, predicted_bottom_date, phase)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'distributing')
            RETURNING id
            """,
            ticker,
            breakout_date,
            today_row["volume"],
            today_row["close"],
            est_dist_days,
            est_dist_days,
            predicted_bottom,
        )

    logger.info(f"M3 Cycle created: {ticker} breakout={breakout_date} id={cycle_id}")

    # Link volume alert → cycle
    if alert_id is not None:
        async with _pool.acquire() as conn:
            await conn.execute(
                "UPDATE volume_alerts SET cycle_event_id=$1 WHERE id=$2",
                cycle_id, alert_id,
            )

    # SSE push
    if _alert_queue is not None:
        await _alert_queue.put({
            "type": "cycle_alert",
            "data": {
                "id": cycle_id,
                "ticker": ticker,
                "breakout_date": str(breakout_date),
                "phase": "distributing",
                "predicted_bottom_date": str(predicted_bottom),
            },
        })

    asyncio.create_task(notification.send_cycle_breakout_email(cycle_id))


async def _update_cycle(ticker: str, cycle: dict, recent_rows: list, ma20: float):
    cycle_id = cycle["id"]
    breakout_date = cycle["breakout_date"]
    today = date.today()
    elapsed = count_trading_days_between(breakout_date, today)
    est_days = cycle["estimated_dist_days"] or 20
    remaining = max(0, est_days - elapsed)

    # 10-day warning
    if remaining <= settings.ALERT_DAYS_BEFORE_CYCLE and not cycle["alert_sent_10d"]:
        async with _pool.acquire() as conn:
            await conn.execute(
                "UPDATE cycle_events SET alert_sent_10d=TRUE WHERE id=$1", cycle_id
            )
        asyncio.create_task(notification.send_cycle_10day_warning_email(cycle_id))
        logger.info(f"M3 10-day warning sent: {ticker} cycle_id={cycle_id}")

    # Bottom detection: 3 consecutive days vol < 50% MA20
    last_vols = [r["volume"] for r in recent_rows[-3:] if r["volume"]]
    all_low = len(last_vols) == 3 and all(v < ma20 * 0.5 for v in last_vols)

    if all_low and remaining <= 5 and not cycle["alert_sent_bottom"]:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE cycle_events
                SET phase='bottoming', alert_sent_bottom=TRUE,
                    trading_days_elapsed=$2, days_remaining=$3, updated_at=NOW()
                WHERE id=$1
                """,
                cycle_id,
                elapsed,
                remaining,
            )
        asyncio.create_task(notification.send_cycle_bottom_email(cycle_id))
        logger.info(f"M3 Bottom alert: {ticker} cycle_id={cycle_id} elapsed={elapsed}d")
    else:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE cycle_events
                SET days_remaining=$2, trading_days_elapsed=$3,
                    distributed_so_far=$3, updated_at=NOW()
                WHERE id=$1
                """,
                cycle_id,
                remaining,
                elapsed,
            )
