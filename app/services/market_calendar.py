"""Market calendar service — seeding and lookup."""
import logging
from datetime import date, timedelta
from app.utils.trading_hours import is_trading_day, NON_TRADING_DAYS_2026

logger = logging.getLogger(__name__)


async def seed_market_calendar(pool, year: int = 2026):
    """Seed market_calendar table for the given year."""
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    rows = []
    d = start
    while d <= end:
        trading = is_trading_day(d)
        reason = None
        if not trading and d.weekday() < 5:
            # It's a weekday but non-trading — it's a holiday
            reason = "holiday"
        elif not trading:
            reason = "weekend"
        rows.append((d, trading, reason))
        d += timedelta(days=1)

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO market_calendar (date, is_trading_day, reason)
            VALUES ($1, $2, $3)
            ON CONFLICT (date) DO NOTHING
            """,
            rows,
        )
    logger.info(f"Seeded market_calendar for {year}: {len(rows)} days")


async def is_trading_day_db(pool, d: date) -> bool:
    """Check trading day from DB (fallback to local logic)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_trading_day FROM market_calendar WHERE date = $1", d
        )
    if row:
        return row["is_trading_day"]
    # Fallback to local logic
    return is_trading_day(d)
