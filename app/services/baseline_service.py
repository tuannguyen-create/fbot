"""Volume baseline computation service."""
import logging
from datetime import date
from statistics import mean, stdev
from collections import defaultdict
from typing import Optional

from app.config import settings
from app.utils.trading_hours import get_slot, is_trading_day

logger = logging.getLogger(__name__)

_pool = None
_redis = None


def inject_deps(pool, redis):
    global _pool, _redis
    _pool = pool
    _redis = redis


async def rebuild_all():
    """Rebuild baselines for all tickers. Called nightly by APScheduler."""
    if not is_trading_day(date.today()):
        logger.info("Skipping baseline rebuild (non-trading day)")
        return
    logger.info(f"Starting baseline rebuild for {len(settings.WATCHLIST)} tickers")
    for ticker in settings.WATCHLIST:
        try:
            await rebuild_ticker(ticker)
        except Exception as e:
            logger.error(f"Baseline rebuild failed for {ticker}: {e}")
    logger.info("Baseline rebuild complete")


async def rebuild_ticker(ticker: str):
    """Rebuild baselines for a single ticker from intraday_1m history."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh' AS bar_ict,
                volume
            FROM intraday_1m
            WHERE ticker = $1
              AND bar_time >= NOW() - INTERVAL '25 days'
              AND volume > 0
            ORDER BY bar_time ASC
            """,
            ticker,
        )

    if not rows:
        logger.debug(f"No intraday data for {ticker}, skipping baseline rebuild")
        return

    # Group volumes by slot
    slot_volumes: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        bar_ict = row["bar_ict"]
        slot = get_slot(bar_ict.time())
        if slot is not None and row["volume"] > 0:
            slot_volumes[slot].append(row["volume"])

    now_date = date.today()
    upsert_rows = []
    for slot, vols in slot_volumes.items():
        avg_5d: Optional[int] = int(mean(vols[-5:])) if len(vols) >= 5 else (int(mean(vols)) if vols else None)
        avg_20d: Optional[int] = int(mean(vols[-20:])) if len(vols) >= 20 else None
        std_dev_val: Optional[int] = int(stdev(vols[-20:])) if len(vols) >= 20 else None
        upsert_rows.append((ticker, slot, avg_5d, avg_20d, std_dev_val, len(vols), now_date))

    async with _pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO volume_baselines (ticker, slot, avg_5d, avg_20d, std_dev, sample_count, updated_date)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (ticker, slot)
            DO UPDATE SET avg_5d=$3, avg_20d=$4, std_dev=$5, sample_count=$6, updated_date=$7
            """,
            upsert_rows,
        )

    # Update Redis cache
    async with _redis.pipeline() as pipe:
        for ticker_val, slot, avg_5d, avg_20d, std_dev_val, _, _ in upsert_rows:
            key = f"baseline:{ticker_val}:{slot}"
            mapping = {}
            if avg_5d is not None:
                mapping["avg_5d"] = str(avg_5d)
            if avg_20d is not None:
                mapping["avg_20d"] = str(avg_20d)
            if std_dev_val is not None:
                mapping["std_dev"] = str(std_dev_val)
            if mapping:
                await pipe.hset(key, mapping=mapping)
                await pipe.expire(key, 86400)
        await pipe.execute()

    logger.debug(f"Rebuilt baselines for {ticker}: {len(upsert_rows)} slots")


async def get_baseline(ticker: str, slot: int) -> Optional[dict]:
    """Get baseline for ticker+slot. Redis first, fallback to DB."""
    key = f"baseline:{ticker}:{slot}"
    cached = await _redis.hgetall(key)
    if cached and "avg_5d" in cached:
        return {k: int(v) for k, v in cached.items()}

    # DB fallback
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT avg_5d, avg_20d, std_dev FROM volume_baselines WHERE ticker=$1 AND slot=$2",
            ticker,
            slot,
        )
    if row:
        result = {k: v for k, v in row.items() if v is not None}
        # Cache in Redis
        if result:
            mapping = {k: str(v) for k, v in result.items()}
            await _redis.hset(key, mapping=mapping)
            await _redis.expire(key, 86400)
        return result
    return None


async def check_first_run_backfill() -> bool:
    """Return True if baselines table is empty (needs backfill)."""
    async with _pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM volume_baselines")
    return (count or 0) == 0
