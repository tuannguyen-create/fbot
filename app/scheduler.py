"""APScheduler setup — AsyncIOScheduler with in-memory job store."""
import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.utils.trading_hours import is_trading_day
from app.services import baseline_service, alert_engine_m3, market_calendar, daily_ohlcv_service

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")


async def _job_baseline_rebuild():
    if not is_trading_day(date.today()):
        logger.info("Scheduler: skip baseline rebuild (non-trading day)")
        return
    await baseline_service.rebuild_all()


async def _job_m3_daily():
    if not is_trading_day(date.today()):
        logger.info("Scheduler: skip M3 daily (non-trading day)")
        return
    await alert_engine_m3.run_daily()


async def _job_daily_ohlcv_aggregate():
    if not is_trading_day(date.today()):
        return
    await daily_ohlcv_service.aggregate_today()


async def _job_cleanup_intraday():
    """Delete intraday_1m data older than 25 days."""
    from app.database import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM intraday_1m WHERE bar_time < NOW() - INTERVAL '25 days'"
        )
    # result = "DELETE N"
    deleted = int(result.split()[-1]) if result else 0
    logger.info(f"Cleanup: removed {deleted} old intraday rows")


def setup_jobs():
    # Baseline rebuild at 18:00 ICT every day
    scheduler.add_job(
        _job_baseline_rebuild,
        trigger="cron",
        hour=18,
        minute=0,
        id="baseline_rebuild",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Aggregate intraday_1m → daily_ohlcv at 15:05 ICT (must run before M3 reads daily_ohlcv)
    scheduler.add_job(
        _job_daily_ohlcv_aggregate,
        trigger="cron",
        hour=15,
        minute=5,
        id="daily_ohlcv_aggregate",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # M3 daily analysis at 15:10 ICT (after daily_ohlcv is aggregated)
    scheduler.add_job(
        _job_m3_daily,
        trigger="cron",
        hour=15,
        minute=10,
        id="m3_daily",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Cleanup old intraday data at 19:00 ICT
    scheduler.add_job(
        _job_cleanup_intraday,
        trigger="cron",
        hour=19,
        minute=0,
        id="cleanup_intraday",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler started")
