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
_startup_at: Optional[datetime] = None   # set when start() is called
_event = None
_client = None
_watchdog_task: Optional[asyncio.Task] = None

BACKOFF_BASE = 60           # Minimum wait before reconnect (unplanned crash)
BACKOFF_MAX = 300           # 5 min max between retries
_STALE_MINUTES = 10         # Watchdog threshold: no data for N min during trading hours
_PROACTIVE_RESTART_SECS = 55 * 60  # Restart before 1-hour FiinQuantX JWT expires
_CONNECT_TIMEOUT_SECS = 120  # After this, "never connected during trading hours" = error
# After event.stop(), server receives proper CLOSE frames and de-registers connections
# quickly. 90s is sufficient (vs 360s needed when close() was a no-op).
_PROACTIVE_RESTART_WAIT = 90


def inject_deps(pool, redis, alert_queue: asyncio.Queue):
    global _pool, _redis, _alert_queue
    _pool = pool
    _redis = redis
    _alert_queue = alert_queue
    # Propagate to engines
    alert_engine_m1.inject_deps(pool, redis, alert_queue)


def get_status() -> str:
    return "connected" if _stream_connected else "disconnected"


def _current_session_open_utc(now_ict) -> Optional[datetime]:
    """Return UTC time when today's active trading session most recently opened.

    If it is morning session (9:00–11:30 ICT) → returns today 09:00 ICT as UTC.
    If it is afternoon session (13:00–14:30 ICT) → returns today 13:00 ICT as UTC.
    Called only when is_trading_hours() is already True, so no other case occurs.
    """
    from zoneinfo import ZoneInfo
    from app.utils.trading_hours import BREAK_END
    ict_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    d = now_ict.date()
    # is_trading_hours is True → either morning (9:00-11:30) or afternoon (13:00-14:30)
    if now_ict.time() >= BREAK_END:
        open_dt = datetime(d.year, d.month, d.day, 13, 0, tzinfo=ict_tz)
    else:
        open_dt = datetime(d.year, d.month, d.day, 9, 0, tzinfo=ict_tz)
    return open_dt.astimezone(timezone.utc)


def get_detailed_status() -> dict:
    """Return stream status with reason and last_bar_time for rich UI display."""
    from zoneinfo import ZoneInfo
    from app.utils.trading_hours import is_trading_hours, is_trading_day

    last_iso = _last_bar_time.isoformat() if _last_bar_time else None

    if _stream_connected:
        return {"status": "connected", "reason": None, "last_bar_time": last_iso}

    now_utc = datetime.now(timezone.utc)
    now_ict = now_utc.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))

    if not is_trading_day(now_ict.date()) or not is_trading_hours(now_ict.time()):
        reason = "outside_hours"
    elif _last_bar_time is None:
        # Never received a bar this process lifetime — initial connect window
        elapsed_startup = (now_utc - _startup_at).total_seconds() if _startup_at else 9999
        reason = "connecting" if elapsed_startup < _CONNECT_TIMEOUT_SECS else "error"
    elif (now_utc - _last_bar_time).total_seconds() < 300:
        # Had data recently — temporary disconnect, expect recovery
        reason = "reconnecting"
    else:
        # Last bar is stale. Check whether it predates today's session open —
        # if so the process ran overnight and hasn't yet received today's first bar.
        # Apply the same connecting grace period rather than immediately showing error.
        session_open_utc = _current_session_open_utc(now_ict)
        if _last_bar_time < session_open_utc:
            elapsed_since_open = (now_utc - session_open_utc).total_seconds()
            reason = "connecting" if elapsed_since_open < _CONNECT_TIMEOUT_SECS else "error"
        else:
            # Bar is from today's session but stale >5 min — real error
            reason = "error"

    return {"status": "disconnected", "reason": reason, "last_bar_time": last_iso}


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


async def _close_event():
    """Stop stream without blocking event loop.

    event.stop() calls ConnectionStateChecker.stop() → thread.join(timeout=10)
    for each of the 33 SignalR connections. Running it in an executor prevents
    blocking the asyncio event loop during proactive restarts or crashes.
    """
    global _event, _client, _stream_connected
    _stream_connected = False
    if _event:
        event_ref, _event = _event, None  # grab ref before clearing global
        try:
            await asyncio.get_event_loop().run_in_executor(None, event_ref.stop)
            logger.info("FiinQuantX event stopped successfully")
        except Exception as e:
            logger.error(f"FiinQuantX event.stop() failed: {e}")
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
            await _close_event()


async def _proactive_restart_timer():
    """Close stream before 1-hour JWT expires to prevent mass-reconnect cascade."""
    await asyncio.sleep(_PROACTIVE_RESTART_SECS)
    if _stream_connected:
        logger.info("Proactive restart: closing stream before JWT expires (55 min timer)")
        await _close_event()


async def start():
    """Start stream with infinite retry. Non-blocking (runs in thread executor)."""
    global _stream_connected, _loop, _watchdog_task, _startup_at
    _loop = asyncio.get_running_loop()
    _startup_at = datetime.now(timezone.utc)

    _watchdog_task = asyncio.create_task(_watchdog())

    attempt = 0
    while True:
        attempt += 1
        run_start = _loop.time()
        refresh_task = asyncio.create_task(_proactive_restart_timer())
        try:
            logger.info(f"FiinQuantX stream starting (attempt {attempt})")
            await _loop.run_in_executor(None, _stream_blocking)
            logger.info("FiinQuantX stream disconnected")
        except ImportError:
            logger.warning("FiinQuantX not installed — stream disabled (dev mode)")
            await _close_event()
            refresh_task.cancel()
            _watchdog_task.cancel()
            return
        except asyncio.CancelledError:
            logger.info("Stream task cancelled")
            await _close_event()
            refresh_task.cancel()
            _watchdog_task.cancel()
            raise
        except Exception as e:
            logger.error(f"Stream error (attempt {attempt}): {e}", exc_info=True)
        finally:
            await _close_event()
            refresh_task.cancel()

        elapsed = _loop.time() - run_start
        # Planned restart (proactive JWT refresh): wait for server to clean up stale sessions
        if elapsed > _PROACTIVE_RESTART_SECS - 60:
            attempt = 0
            wait = _PROACTIVE_RESTART_WAIT
        # Unplanned crash but session ran > 5 min: quick reconnect, reset penalty
        elif elapsed > 300:
            attempt = 0
            wait = BACKOFF_BASE
        # Fast crash (likely stale server sessions): exponential backoff
        else:
            wait = min(BACKOFF_BASE * (2 ** min(attempt - 1, 6)), BACKOFF_MAX)

        logger.info(f"Reconnecting in {wait}s (session ran {elapsed:.0f}s)...")
        await asyncio.sleep(wait)


async def stop():
    global _stream_connected, _watchdog_task
    await _close_event()
    if _watchdog_task:
        _watchdog_task.cancel()
        _watchdog_task = None
    logger.info("FiinQuantX stream stopped")
