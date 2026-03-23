"""FiinQuantX WebSocket stream ingester."""
import asyncio
import logging
from datetime import datetime, timezone, time as dtime
from typing import Optional

from app.config import settings
from app.services import alert_engine_m1, baseline_service

logger = logging.getLogger(__name__)

_pool = None
_redis = None
_alert_queue = None
_loop = None  # event loop captured in async context for thread-safe coroutine scheduling

# Stream state
_stream_connected = False
_last_bar_time: Optional[datetime] = None
_event = None
_client = None
_watchdog_task: Optional[asyncio.Task] = None

BACKOFF_BASE = 5
BACKOFF_MAX = 300  # 5 min max between retries
_STALE_MINUTES = 10  # Watchdog threshold: no data for N min during trading hours


def inject_deps(pool, redis, alert_queue: asyncio.Queue):
    global _pool, _redis, _alert_queue
    _pool = pool
    _redis = redis
    _alert_queue = alert_queue
    # Propagate to engines
    alert_engine_m1.inject_deps(pool, redis, alert_queue)


def get_status() -> str:
    return "connected" if _stream_connected else "disconnected"


def get_last_bar_time() -> Optional[datetime]:
    return _last_bar_time


def _parse_bar(raw: dict) -> Optional[dict]:
    """Parse raw FiinQuantX callback dict → normalized bar."""
    try:
        # FiinQuantX returns various field names — handle both cases
        ticker = raw.get("ticker") or raw.get("Symbol") or raw.get("symbol")
        if not ticker:
            return None

        # bar_time: try common field names
        bar_time_raw = (
            raw.get("datetime")
            or raw.get("Date")
            or raw.get("date")
            or raw.get("time")
        )
        if bar_time_raw is None:
            return None

        if isinstance(bar_time_raw, str):
            bar_time_raw = bar_time_raw.replace("Z", "+00:00")
            bar_time = datetime.fromisoformat(bar_time_raw)
        elif isinstance(bar_time_raw, datetime):
            bar_time = bar_time_raw
        else:
            bar_time = datetime.fromtimestamp(float(bar_time_raw), tz=timezone.utc)

        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)

        def _int(v, default=0):
            try:
                return int(v) if v is not None else default
            except (ValueError, TypeError):
                return default

        def _float(v, default=0.0):
            try:
                return float(v) if v is not None else default
            except (ValueError, TypeError):
                return default

        bar = {
            "ticker": str(ticker).upper(),
            "bar_time": bar_time,
            "open": _float(raw.get("open") or raw.get("Open")),
            "high": _float(raw.get("high") or raw.get("High")),
            "low": _float(raw.get("low") or raw.get("Low")),
            "close": _float(raw.get("close") or raw.get("Close")),
            "volume": _int(raw.get("volume") or raw.get("Volume")),
            # bu/sd = INTEGER counts (NOT percentages)
            "bu": _int(raw.get("bu") or raw.get("BU")),
            "sd": _int(raw.get("sd") or raw.get("SD")),
            "fb": _int(raw.get("fb") or raw.get("FB")),
            "fs": _int(raw.get("fs") or raw.get("FS")),
            "fn": _int(raw.get("fn") or raw.get("FN")),
        }
        return bar
    except Exception as e:
        logger.warning(f"Failed to parse bar: {raw} — {e}")
        return None


def _on_data(raw):
    """FiinQuantX callback — called from stream thread. Schedule on event loop."""
    global _last_bar_time
    bar = _parse_bar(raw)
    if bar and _loop is not None:
        _last_bar_time = bar["bar_time"]
        asyncio.run_coroutine_threadsafe(_process_bar(bar), _loop)


async def _process_bar(bar: dict):
    """Process one 1m bar: persist + run alert engines."""
    try:
        ticker = bar["ticker"]
        # Skip if not in watchlist
        if ticker not in settings.WATCHLIST:
            return
        # Persist to intraday_1m
        await _save_bar(bar)
        # Run M1 volume scanner
        await alert_engine_m1.process(bar)
        # M3 is daily-only (runs via APScheduler)
    except Exception as e:
        logger.error(f"_process_bar error for {bar.get('ticker')}: {e}", exc_info=True)


async def _save_bar(bar: dict):
    """Upsert 1m bar into intraday_1m."""
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO intraday_1m
                    (ticker, bar_time, open, high, low, close, volume, bu, sd, fb, fs, fn)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (ticker, bar_time) DO UPDATE
                    SET volume=$7, bu=$8, sd=$9, fb=$10, fs=$11, fn=$12
                """,
                bar["ticker"],
                bar["bar_time"],
                bar["open"],
                bar["high"],
                bar["low"],
                bar["close"],
                bar["volume"],
                bar["bu"],
                bar["sd"],
                bar["fb"],
                bar["fs"],
                bar["fn"],
            )
    except Exception as e:
        logger.error(f"_save_bar error: {e}")


def _stream_blocking():
    """Run blocking FiinQuantX stream. Called in thread executor."""
    global _stream_connected, _event, _client
    import FiinQuantX as fq

    _client = fq.FiinSession(
        username=settings.FIINQUANT_USERNAME,
        password=settings.FIINQUANT_PASSWORD,
    ).login()

    _event = _client.Fetch_Trading_Data(
        realtime=True,
        tickers=settings.WATCHLIST,
        fields=["open", "high", "low", "close", "volume", "bu", "sd", "fb", "fs", "fn"],
        by="1m",
        callback=_on_data,
        period=1,
    )
    _stream_connected = True
    logger.info(f"FiinQuantX stream started for {len(settings.WATCHLIST)} tickers")
    _event.get_data()  # Blocking
    _stream_connected = False
    logger.info("FiinQuantX stream ended")


def _close_event():
    """Close current stream event and clear references to prevent zombie threads."""
    global _event, _client, _stream_connected
    _stream_connected = False
    if _event:
        try:
            _event.close()
        except Exception:
            pass
        _event = None
    _client = None


async def _watchdog():
    """Restart stream if no data received during trading hours for too long."""
    from app.utils.trading_hours import is_trading_day
    from app.utils.timezone import to_ict

    while True:
        await asyncio.sleep(60)

        if not _stream_connected or _last_bar_time is None:
            continue

        now_utc = datetime.now(timezone.utc)
        now_ict = to_ict(now_utc)

        # Only check during trading hours 9:00–15:10 ICT
        if not (dtime(9, 0) <= now_ict.time() <= dtime(15, 10)):
            continue
        if not is_trading_day(now_ict.date()):
            continue

        age_min = (now_utc - _last_bar_time).total_seconds() / 60
        if age_min > _STALE_MINUTES:
            logger.warning(
                f"Stream stale: no data for {age_min:.1f} min — forcing reconnect"
            )
            _close_event()


async def start():
    """Start stream with infinite retry. Non-blocking (runs in thread executor)."""
    global _stream_connected, _loop, _watchdog_task
    _loop = asyncio.get_running_loop()

    _watchdog_task = asyncio.create_task(_watchdog())

    attempt = 0
    while True:
        attempt += 1
        run_start = _loop.time()
        try:
            logger.info(f"FiinQuantX stream starting (attempt {attempt})")
            await _loop.run_in_executor(None, _stream_blocking)
            logger.info("FiinQuantX stream disconnected")
        except ImportError:
            logger.warning("FiinQuantX not installed — stream disabled (dev mode)")
            _close_event()
            _watchdog_task.cancel()
            return
        except asyncio.CancelledError:
            logger.info("Stream task cancelled")
            _close_event()
            _watchdog_task.cancel()
            raise
        except Exception as e:
            logger.error(f"Stream error (attempt {attempt}): {e}", exc_info=True)
        finally:
            _close_event()

        # Reset backoff after a successful long-running session (> 5 min)
        if _loop.time() - run_start > 300:
            attempt = 0

        wait = min(BACKOFF_BASE * (2 ** min(attempt - 1, 6)), BACKOFF_MAX)
        logger.info(f"Reconnecting in {wait}s...")
        await asyncio.sleep(wait)


async def stop():
    global _stream_connected, _watchdog_task
    _close_event()
    if _watchdog_task:
        _watchdog_task.cancel()
        _watchdog_task = None
    logger.info("FiinQuantX stream stopped")
