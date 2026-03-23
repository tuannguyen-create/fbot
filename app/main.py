"""FastAPI application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup & shutdown logic."""
    # --- STARTUP ---
    from app import database, redis_client
    from app.services import baseline_service, notification, stream_ingester
    from app.services import alert_engine_m1, alert_engine_m3
    from app.services import market_calendar
    from app.scheduler import setup_jobs
    from app.api.stream import alert_queue, broadcaster

    # 1. DB
    try:
        pool = await database.init_pool()
    except Exception as e:
        logger.critical(f"DB connection failed: {e}")
        raise

    # 2. Run migrations
    try:
        await _run_alembic_migrations()
    except Exception as e:
        logger.warning(f"Migration skipped (may already be applied): {e}")

    # 3. Redis
    try:
        redis = await redis_client.init_redis()
    except Exception as e:
        logger.warning(f"Redis not available: {e}. Continuing without cache.")
        redis = None

    # 4. Seed data
    await _seed_watchlist(pool)
    await market_calendar.seed_market_calendar(pool, 2026)

    # 5. Inject deps
    baseline_service.inject_deps(pool, redis)
    notification.inject_deps(pool)
    stream_ingester.inject_deps(pool, redis, alert_queue)
    alert_engine_m1.inject_deps(pool, redis, alert_queue)
    alert_engine_m3.inject_deps(pool, redis, alert_queue)

    # 6. Warm in-memory baseline cache + first-run backfill check
    await baseline_service.warm_cache()
    needs_backfill = await baseline_service.check_first_run_backfill()
    if needs_backfill:
        logger.info("No baselines found — will backfill when FiinQuantX connects")

    # 7. APScheduler
    setup_jobs()

    # 8. Start SSE broadcaster + FiinQuantX stream (background tasks)
    broadcaster_task = asyncio.create_task(broadcaster())
    stream_task = asyncio.create_task(stream_ingester.start())

    logger.info("fbot backend started ✓")

    yield

    # --- SHUTDOWN ---
    broadcaster_task.cancel()
    stream_task.cancel()
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
from app.api import alerts, cycles, watchlist, settings, stream

app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["alerts"])
app.include_router(cycles.router, prefix="/api/v1/cycles", tags=["cycles"])
app.include_router(watchlist.router, prefix="/api/v1/watchlist", tags=["watchlist"])
app.include_router(settings.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(stream.router, prefix="/api/v1/stream", tags=["stream"])


@app.get("/api/v1/health")
async def health():
    from app import database, redis_client
    from app.services import stream_ingester

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

    return {
        "success": True,
        "data": {
            "db": db_status,
            "redis": redis_status,
            "stream": stream_ingester.get_status(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


@app.get("/")
async def root():
    return {"success": True, "data": {"message": "fbot API running", "docs": "/docs"}}
