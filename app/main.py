"""FastAPI application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from datetime import date as _date

from app.config import settings
from app.utils.logger import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


async def _run_alembic_migrations():
    """Run DB migrations on startup."""
    import subprocess
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f"Alembic migration failed:\n{result.stderr}")
        raise RuntimeError("DB migration failed")
    logger.info("DB migrations applied")


async def _seed_watchlist(pool):
    """Seed watchlist table if empty."""
    from app.api.watchlist import WATCHLIST_COMPANY_NAMES
    from app.config import settings

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM watchlist")
        if count > 0:
            return
        vn30_set = set(settings.WATCHLIST[:30])
        rows = [
            (t, WATCHLIST_COMPANY_NAMES.get(t), "HOSE", None, t in vn30_set, True)
            for t in settings.WATCHLIST
        ]
        await conn.executemany(
            """
            INSERT INTO watchlist (ticker, company_name, exchange, sector, in_vn30, active)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (ticker) DO NOTHING
            """,
            rows,
        )
    logger.info(f"Watchlist seeded: {len(rows)} tickers")


async def _maybe_bootstrap_historical_replays(pool):
    """Seed historical M1/M3 into UI tables once when no replay rows exist yet.

    M3 uses daily_ohlcv (1D historical) — works on all FiinQuantX plans.
    M1 uses intraday_1m — plan-dependent. If the current plan's intraday
    history window yields 0 bars, M1 bootstrap is skipped gracefully.
    M1 historical replay is also available via POST /admin/replay-m1-history.
    """
    from app.services import alert_engine_m1, alert_engine_m3
    from app.services import historical_intraday_service

    async with pool.acquire() as conn:
        m1_hist_count = await conn.fetchval(
            "SELECT COUNT(*) FROM volume_alerts WHERE origin <> 'live'"
        )
        m3_hist_count = await conn.fetchval(
            "SELECT COUNT(*) FROM cycle_events WHERE origin <> 'live'"
        )

    # M1: try intraday backfill within plan-aware window, then replay if data exists
    if (m1_hist_count or 0) == 0:
        if await historical_intraday_service.check_needs_backfill():
            logger.info("intraday_1m sparse — attempting plan-aware 1m historical backfill")
            bars_fetched = await historical_intraday_service.backfill_intraday()
            if bars_fetched > 0:
                logger.info("M1 bootstrap: intraday data available — replaying M1 history")
                await alert_engine_m1.replay_m1_history(
                    days=25,
                    apply=True,
                    mode="bootstrap",
                    notify_mode="digest",
                )
            else:
                logger.info(
                    "M1 bootstrap skipped: intraday history unavailable for current "
                    "FiinQuantX plan. M1 alerts will populate from live tick stream."
                )

    if (m3_hist_count or 0) == 0:
        logger.info("No historical M3 cycles found — bootstrapping 25-day M3 replay")
        await alert_engine_m3.replay_history(
            days=25,
            apply=True,
            mode="bootstrap",
            notify_mode="digest",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup & shutdown logic."""
    # --- STARTUP ---
    from app import database, redis_client
    from app.services import baseline_service, notification, stream_ingester, universe_service
    from app.services import alert_engine_m1, alert_engine_m3
    from app.services import market_calendar, daily_ohlcv_service
    from app.services import historical_intraday_service
    from app.scheduler import setup_jobs
    from app.api.stream import alert_queue, broadcaster

    # 1. DB
    try:
        pool = await database.init_pool()
    except Exception as e:
        logger.critical(f"DB connection failed: {e}")
        raise

    # 2. Run migrations — fail-fast: a schema mismatch is fatal
    await _run_alembic_migrations()

    # 3. Redis
    try:
        redis = await redis_client.init_redis()
    except Exception as e:
        logger.warning(f"Redis not available: {e}. Continuing without cache.")
        redis = None

    # 4. Seed data
    await _seed_watchlist(pool)
    _today = _date.today()
    await market_calendar.seed_market_calendar(pool, _today.year)
    await market_calendar.seed_market_calendar(pool, _today.year + 1)

    # 5. Inject deps
    baseline_service.inject_deps(pool, redis)
    notification.inject_deps(pool, redis)
    stream_ingester.inject_deps(pool, redis, alert_queue)
    alert_engine_m1.inject_deps(pool, redis, alert_queue)
    alert_engine_m3.inject_deps(pool, redis, alert_queue)
    daily_ohlcv_service.inject_deps(pool)
    historical_intraday_service.inject_deps(pool)
    universe_service.inject_deps(pool)

    # Clean up live M1 alerts that were left in `fired` by a previous session
    # (restart, end-of-day, or missing downstream bars).
    await alert_engine_m1.expire_stale_fired_alerts()

    # 6. Warm in-memory baseline cache + first-run backfill
    await baseline_service.warm_cache()
    needs_backfill = await baseline_service.check_first_run_backfill()
    if needs_backfill:
        logger.info("No baselines found — triggering rebuild")
        asyncio.create_task(baseline_service.rebuild_all(force=True))

    # 7. APScheduler
    setup_jobs()

    # 8. Start SSE broadcaster + FiinQuantX stream (background tasks)
    broadcaster_task = asyncio.create_task(broadcaster())
    stream_task = asyncio.create_task(stream_ingester.start())

    # Backfill daily OHLCV + bootstrap historical replays.
    # Wait until stream connects (up to 60 s) to avoid racing FiinQuantX
    # session cleanup at boot.
    #
    # M3 bootstrap: always works (uses daily 1D historical).
    # M1 bootstrap: plan-dependent — if FiinQuantX plan allows intraday
    # history, backfill_intraday() seeds data and M1 replay runs.
    # Otherwise M1 populates from live tick stream over ~5 trading days.
    async def _delayed_backfill():
        for _ in range(60):
            if stream_ingester.get_status() == "connected":
                break
            await asyncio.sleep(1)
        await daily_ohlcv_service.backfill_historical()
        await _maybe_bootstrap_historical_replays(pool)
    backfill_task = asyncio.create_task(_delayed_backfill())

    logger.info("fbot backend started ✓")

    yield

    # --- SHUTDOWN ---
    broadcaster_task.cancel()
    stream_task.cancel()
    backfill_task.cancel()
    await stream_ingester.stop()
    from app.scheduler import scheduler
    scheduler.shutdown(wait=False)
    await database.close_pool()
    if redis:
        await redis_client.close_redis()
    logger.info("fbot backend stopped")


app = FastAPI(
    title="fbot API",
    description="Vietnam stock market alert system",
    version="1.0.0",
    lifespan=lifespan,
)

_cors_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
if settings.FRONTEND_URL and settings.FRONTEND_URL not in _cors_origins:
    _cors_origins.append(settings.FRONTEND_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Exception handlers ---
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "Internal server error"},
    )


# --- Routers ---
from app.api import alerts, cycles, watchlist, stream, admin, notifications
from app.api import settings as settings_router

app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["alerts"])
app.include_router(cycles.router, prefix="/api/v1/cycles", tags=["cycles"])
app.include_router(watchlist.router, prefix="/api/v1/watchlist", tags=["watchlist"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["notifications"])
app.include_router(stream.router, prefix="/api/v1/stream", tags=["stream"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])


@app.get("/api/v1/health")
async def health():
    from app import database, redis_client
    from app.services import stream_ingester, universe_service

    db_status = "ok"
    redis_status = "ok"

    try:
        pool = database.get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception:
        db_status = "error"

    try:
        r = redis_client.get_redis()
        if r is None:
            redis_status = "disabled"
        else:
            await r.ping()
    except Exception:
        redis_status = "error"

    stream_detail = stream_ingester.get_detailed_status()
    active_tickers = await universe_service.get_active_tickers()
    telegram_configured = bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_IDS.strip())
    email_configured = bool(settings.RESEND_API_KEY and settings.RESEND_TO.strip())

    return {
        "success": True,
        "data": {
            "db": db_status,
            "redis": redis_status,
            "stream": stream_detail["status"],
            "stream_reason": stream_detail["reason"],
            "last_bar_time": stream_detail["last_bar_time"],
            "active_ticker_count": len(active_tickers),
            "effective_ticker_count": min(len(active_tickers), settings.EFFECTIVE_STREAM_TICKER_LIMIT),
            "fiinquant_ticker_limit": settings.EFFECTIVE_STREAM_TICKER_LIMIT,
            "effective_stream_ticker_count": min(len(active_tickers), settings.EFFECTIVE_STREAM_TICKER_LIMIT),
            "fiinquant_stream_ticker_limit": settings.EFFECTIVE_STREAM_TICKER_LIMIT,
            "effective_intraday_ticker_count": min(len(active_tickers), settings.EFFECTIVE_INTRADAY_TICKER_LIMIT),
            "fiinquant_intraday_ticker_limit": settings.EFFECTIVE_INTRADAY_TICKER_LIMIT,
            "effective_daily_ticker_count": len(active_tickers),
            "telegram_configured": telegram_configured,
            "email_configured": email_configured,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@app.get("/")
async def root():
    return {"success": True, "data": {"message": "fbot API running", "docs": "/docs"}}
