"""APScheduler setup — AsyncIOScheduler with in-memory job store."""
import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.utils.trading_hours import is_trading_day
from app.services import baseline_service, alert_engine_m3, market_calendar

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


async def _job_cleanup_intraday():
    """Delete intraday_1m data older than 25 days."""
    from app.database import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM intraday_1m WHERE bar_time < NOW() - INTERVAL '25 days' RETURNING COUNT(*)"
        )
    logger.info(f"Cleanup: removed old intraday rows (approx {deleted})")


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

    # M3 daily analysis at 15:05 ICT (5 min after close)
    scheduler.add_job(
        _job_m3_daily,
        trigger="cron",
        hour=15,
        minute=5,
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
