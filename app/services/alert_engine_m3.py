"""Module 3: Cycle Analysis Alert Engine (meeting-goc v1.5)."""
import asyncio
import logging
from datetime import date
from statistics import mean
from typing import Optional

from app.config import settings
from app.utils.trading_hours import is_trading_day, count_trading_days_between, add_trading_days
from app.services import notification

logger = logging.getLogger(__name__)

_pool = None
_redis = None
_alert_queue = None

# Phase names — match meeting-goc state machine
PHASE_DISTRIBUTION = "distribution_in_progress"
PHASE_BOTTOMING    = "bottoming_candidate"
PHASE_INVALIDATED  = "invalidated"
PHASES_ACTIVE      = (PHASE_DISTRIBUTION, PHASE_BOTTOMING)

# Breakout zone: cycle invalidated if close drops below zone_low
_BREAKOUT_ZONE_DOWN = 0.97   # −3% of breakout_price
_BREAKOUT_ZONE_UP   = 1.05   # +5% of breakout_price (reference)
# Rewatch window: opens when distribution estimated to end
_REWATCH_WINDOW_DAYS = 10


def inject_deps(pool, redis, alert_queue=None):
    global _pool, _redis, _alert_queue
    _pool = pool
    _redis = redis
    if alert_queue is not None:
        _alert_queue = alert_queue


async def _get_ticker_meta(ticker: str) -> dict:
    """Return {eligible_for_m3, game_type} from watchlist. Defaults to eligible/institutional."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT eligible_for_m3, game_type FROM watchlist WHERE ticker=$1",
            ticker,
        )
    if row:
        return {
            "eligible": row["eligible_for_m3"] is not False,
            "game_type": row["game_type"] or "institutional",
        }
    return {"eligible": True, "game_type": "institutional"}


async def check_intraday_breakout(ticker: str, bar: dict, alert_id: Optional[int] = None):
    """
    Called by M1 when a volume spike fires. Checks if cumulative intraday
    volume already crosses M3 breakout threshold — no need to wait until 15:10.
    """
    meta = await _get_ticker_meta(ticker)
    if not meta["eligible"]:
        return

    # Skip if active distribution cycle already exists
    async with _pool.acquire() as conn:
        active = await conn.fetchrow(
            "SELECT id FROM cycle_events WHERE ticker=$1 AND phase=$2",
            ticker, PHASE_DISTRIBUTION,
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
        await _create_cycle(ticker, fake_row, ma20, meta["game_type"],
                            vol_ratio=vol_ratio, price_chg=price_chg, alert_id=alert_id)


async def run_daily():
    """
    APScheduler calls this at 15:10 ICT (after daily_ohlcv aggregated at 15:05).
    Only runs on M3-eligible tickers.
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
    meta = await _get_ticker_meta(ticker)
    if not meta["eligible"]:
        logger.debug(f"M3 skip {ticker}: not eligible")
        return

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
                "SELECT id FROM cycle_events WHERE ticker=$1 AND phase=$2",
                ticker, PHASE_DISTRIBUTION,
            )
        if not active:
            async with _pool.acquire() as conn:
                today_alert_id = await conn.fetchval(
                    """
                    SELECT id FROM volume_alerts
                    WHERE ticker=$1
                      AND DATE(fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh') = $2
                    ORDER BY ratio_5d DESC NULLS LAST
                    LIMIT 1
                    """,
                    ticker, today_row["date"],
                )
            await _create_cycle(ticker, today_row, ma20, meta["game_type"],
                                vol_ratio=vol_ratio, price_chg=price_chg,
                                alert_id=today_alert_id)
            return

    # --- Update existing active cycles ---
    async with _pool.acquire() as conn:
        cycles = await conn.fetch(
            "SELECT * FROM cycle_events WHERE ticker=$1 AND phase = ANY($2::text[])",
            ticker, list(PHASES_ACTIVE),
        )
    for cycle in cycles:
        await _update_cycle(ticker, dict(cycle), rows, ma20)


async def _create_cycle(
    ticker: str,
    today_row,
    ma20: float,
    game_type: str,
    vol_ratio: float = 0.0,
    price_chg: float = 0.0,
    alert_id: Optional[int] = None,
):
    est_dist_days = 20
    breakout_date = today_row["date"]
    breakout_price = float(today_row["close"] or 0)

    # Rewatch window: opens when estimated distribution ends, stays 10 trading days
    rewatch_start = add_trading_days(breakout_date, est_dist_days)
    rewatch_end = add_trading_days(rewatch_start, _REWATCH_WINDOW_DAYS)

    zone_low  = round(breakout_price * _BREAKOUT_ZONE_DOWN, 2)
    zone_high = round(breakout_price * _BREAKOUT_ZONE_UP, 2)

    phase_reason = (
        f"Phá vỡ khối lượng {vol_ratio:.1f}x MA20, giá tăng {price_chg:.1%}"
        if vol_ratio > 0
        else "Phá vỡ khối lượng bất thường"
    )

    async with _pool.acquire() as conn:
        cycle_id = await conn.fetchval(
            """
            INSERT INTO cycle_events
                (ticker, breakout_date, peak_volume, breakout_price,
                 estimated_dist_days, days_remaining, predicted_bottom_date, phase,
                 game_type, rewatch_window_start, rewatch_window_end,
                 phase_reason, breakout_zone_low, breakout_zone_high)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13, $14)
            RETURNING id
            """,
            ticker,
            breakout_date,
            today_row["volume"],
            breakout_price,
            est_dist_days,
            est_dist_days,
            rewatch_start,             # predicted_bottom_date kept for backwards compat
            PHASE_DISTRIBUTION,
            game_type,
            rewatch_start,
            rewatch_end,
            phase_reason,
            zone_low,
            zone_high,
        )

    logger.info(
        f"M3 Cycle created: {ticker} breakout={breakout_date} id={cycle_id} "
        f"game={game_type} zone=[{zone_low},{zone_high}] "
        f"rewatch={rewatch_start}~{rewatch_end}"
    )

    if alert_id is not None:
        async with _pool.acquire() as conn:
            await conn.execute(
                "UPDATE volume_alerts SET cycle_event_id=$1 WHERE id=$2",
                cycle_id, alert_id,
            )

    if _alert_queue is not None:
        await _alert_queue.put({
            "type": "cycle_alert",
            "data": {
                "id": cycle_id,
                "ticker": ticker,
                "breakout_date": str(breakout_date),
                "phase": PHASE_DISTRIBUTION,
                "game_type": game_type,
                "rewatch_window_start": str(rewatch_start),
                "rewatch_window_end": str(rewatch_end),
                "breakout_zone_low": zone_low,
                "breakout_zone_high": zone_high,
                "phase_reason": phase_reason,
            },
        })

    asyncio.create_task(notification.send_cycle_breakout_email(cycle_id))


async def _update_cycle(ticker: str, cycle: dict, recent_rows: list, ma20: float):
    cycle_id = cycle["id"]
    breakout_date = cycle["breakout_date"]
    breakout_price = float(cycle.get("breakout_price") or 0)
    today = date.today()
    elapsed = count_trading_days_between(breakout_date, today)
    est_days = cycle["estimated_dist_days"] or 20
    remaining = max(0, est_days - elapsed)

    today_close = float(recent_rows[-1]["close"] or 0) if recent_rows else 0

    # --- Invalidation: price closed below breakout zone ---
    zone_low = float(cycle.get("breakout_zone_low") or 0)
    if not zone_low and breakout_price > 0:
        zone_low = round(breakout_price * _BREAKOUT_ZONE_DOWN, 2)

    if zone_low > 0 and today_close > 0 and today_close < zone_low:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE cycle_events
                SET phase=$2,
                    invalidation_reason=$3,
                    phase_reason=$4,
                    trading_days_elapsed=$5,
                    days_remaining=$6,
                    updated_at=NOW()
                WHERE id=$1
                """,
                cycle_id,
                PHASE_INVALIDATED,
                "price_below_zone",
                f"Giá đóng cửa {today_close:.0f} dưới vùng đột phá {zone_low:.0f}",
                elapsed,
                remaining,
            )
        logger.info(
            f"M3 Cycle invalidated: {ticker} id={cycle_id} "
            f"close={today_close:.0f} < zone_low={zone_low:.0f}"
        )
        return

    # --- 10-day warning ---
    if remaining <= settings.ALERT_DAYS_BEFORE_CYCLE and not cycle["alert_sent_10d"]:
        async with _pool.acquire() as conn:
            await conn.execute(
                "UPDATE cycle_events SET alert_sent_10d=TRUE WHERE id=$1", cycle_id
            )
        asyncio.create_task(notification.send_cycle_10day_warning_email(cycle_id))
        logger.info(f"M3 10-day warning sent: {ticker} cycle_id={cycle_id}")

    # --- Bottoming candidate: 3 consecutive days vol < 50% MA20 ---
    last_vols = [r["volume"] for r in recent_rows[-3:] if r["volume"]]
    all_low = len(last_vols) == 3 and all(v < ma20 * 0.5 for v in last_vols)

    if all_low and remaining <= 0 and not cycle["alert_sent_bottom"]:
        rw_start = today
        rw_end = add_trading_days(today, _REWATCH_WINDOW_DAYS)
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE cycle_events
                SET phase=$2, alert_sent_bottom=TRUE,
                    trading_days_elapsed=$3, days_remaining=$4,
                    rewatch_window_start=$5, rewatch_window_end=$6,
                    phase_reason=$7,
                    updated_at=NOW()
                WHERE id=$1
                """,
                cycle_id,
                PHASE_BOTTOMING,
                elapsed,
                remaining,
                rw_start,
                rw_end,
                "Khối lượng thấp 3 ngày liên tiếp, tín hiệu phân phối kết thúc",
            )
        asyncio.create_task(notification.send_cycle_bottom_email(cycle_id))
        logger.info(f"M3 Bottoming candidate: {ticker} cycle_id={cycle_id} elapsed={elapsed}d")
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
