"""FiinQuantX WebSocket tick stream ingester.

Replaces the 1-minute bar stream (Fetch_Trading_Data) with the per-trade
tick stream (Trading_Data_Stream).  Ticks are aggregated into 1-minute bars
that are saved to intraday_1m and fed to the M1 alert engine.

Flow:
  Trading_Data_Stream callback (_on_tick_raw)
    → _accumulate_tick()          # aggregate into running 1m bar per ticker
    → on minute boundary  → _process_bar()    # DB save + M1 alert engine
    → every 15 s (mid-minute) → _process_partial()  # M1 early detect only, no DB
  _minute_flush_timer() (asyncio task, every 5 s)
    → flushes bars whose minute has passed but no new tick arrived (low-vol stocks)
"""
import asyncio
import logging
import threading as _threading
from datetime import datetime, timezone
from time import monotonic as _mono
from typing import Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import alert_engine_m1, baseline_service, universe_service

logger = logging.getLogger(__name__)

_pool = None
_redis = None
_alert_queue = None
_loop = None

# Stream state
_stream_connected = False
_last_bar_time: Optional[datetime] = None
_startup_at: Optional[datetime] = None
_event = None
_client = None
_watchdog_task: Optional[asyncio.Task] = None
_flush_task: Optional[asyncio.Task] = None  # minute-boundary flush timer

BACKOFF_BASE = 60
BACKOFF_MAX = 300
_STALE_MINUTES = 10
_PROACTIVE_RESTART_SECS = 55 * 60
_CONNECT_TIMEOUT_SECS = 120
_PROACTIVE_RESTART_WAIT = 90

# Timezone constant (all ticks arrive as ICT without tz info)
_ICT = ZoneInfo("Asia/Ho_Chi_Minh")

# Active ticker universe. Refreshed from watchlist DB before each stream session.
_ACTIVE_TICKERS = tuple(settings.WATCHLIST)
_WATCHLIST_SET = frozenset(_ACTIVE_TICKERS)

# ── Tick aggregation state ──────────────────────────────────────────────────
# Per-ticker running 1-minute bar accumulator
_tick_bars: dict = {}
_tick_lock = _threading.Lock()
# Per-ticker: monotonic time of last M1 early-detection check
_last_m1_check: dict = {}
_M1_CHECK_INTERVAL = 15  # seconds between mid-minute M1 calls

# Stop signal: threading.Event set by _close_event() to unblock _stream_blocking().
# Replaces polling the private _event._stop attribute.
_stream_stop_event = _threading.Event()

# _shutting_down: set True by _close_event() before flush, checked in _on_tick_raw().
# Gates the thread callback so no new ticks mutate _tick_bars during shutdown/flush,
# eliminating the race between the tick thread and the asyncio flush+reset path.
_shutting_down: bool = False

# _session_confirmed: set True only after the first tick of the current session.
# Keeps get_detailed_status() from reporting "connected" during the silent window
# between _event.start() and the first tick arriving.
_session_confirmed: bool = False


def inject_deps(pool, redis, alert_queue: asyncio.Queue):
    global _pool, _redis, _alert_queue
    _pool = pool
    _redis = redis
    _alert_queue = alert_queue
    alert_engine_m1.inject_deps(pool, redis, alert_queue)


async def _refresh_active_tickers():
    """Refresh stream ticker universe from watchlist DB."""
    global _ACTIVE_TICKERS, _WATCHLIST_SET
    tickers = await universe_service.get_active_tickers(force_refresh=True)
    normalized = tuple(str(t).upper() for t in tickers if t)
    if not normalized:
        normalized = tuple(settings.WATCHLIST)
    if normalized != _ACTIVE_TICKERS:
        logger.info(
            f"Active stream universe updated: {len(_ACTIVE_TICKERS)} → {len(normalized)} tickers"
        )
    _ACTIVE_TICKERS = normalized
    _WATCHLIST_SET = frozenset(_ACTIVE_TICKERS)


def get_status() -> str:
    return "connected" if (_stream_connected and _session_confirmed) else "disconnected"


def _current_session_open_utc(now_ict) -> Optional[datetime]:
    from app.utils.trading_hours import BREAK_END
    ict_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    d = now_ict.date()
    if now_ict.time() >= BREAK_END:
        open_dt = datetime(d.year, d.month, d.day, 13, 0, tzinfo=ict_tz)
    else:
        open_dt = datetime(d.year, d.month, d.day, 9, 0, tzinfo=ict_tz)
    return open_dt.astimezone(timezone.utc)


def get_detailed_status() -> dict:
    from app.utils.trading_hours import is_trading_hours, is_trading_day

    last_iso = _last_bar_time.isoformat() if _last_bar_time else None

    # Only report "connected" after at least one tick of the current session is received.
    # _stream_connected=True but _session_confirmed=False means the process started the
    # stream but no data has arrived yet — that's "connecting", not "connected".
    if _stream_connected and _session_confirmed:
        return {"status": "connected", "reason": None, "last_bar_time": last_iso}

    now_utc = datetime.now(timezone.utc)
    now_ict = now_utc.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))

    if not is_trading_day(now_ict.date()) or not is_trading_hours(now_ict.time()):
        reason = "outside_hours"
    elif _last_bar_time is None:
        elapsed_startup = (now_utc - _startup_at).total_seconds() if _startup_at else 9999
        reason = "connecting" if elapsed_startup < _CONNECT_TIMEOUT_SECS else "error"
    elif (now_utc - _last_bar_time).total_seconds() < 300:
        reason = "reconnecting"
    else:
        session_open_utc = _current_session_open_utc(now_ict)
        if _last_bar_time < session_open_utc:
            elapsed_since_open = (now_utc - session_open_utc).total_seconds()
            reason = "connecting" if elapsed_since_open < _CONNECT_TIMEOUT_SECS else "error"
        else:
            reason = "error"

    return {"status": "disconnected", "reason": reason, "last_bar_time": last_iso}


def get_last_bar_time() -> Optional[datetime]:
    return _last_bar_time


# ── Tick aggregation helpers ───────────────────────────────────────────────

def _set_last_bar_time(dt: Optional[datetime]):
    """Keep last_bar_time monotonic across partial/completed/flush paths."""
    global _last_bar_time
    if dt is None:
        return
    if _last_bar_time is None or dt > _last_bar_time:
        _last_bar_time = dt

def _emit_bar(ticker: str, acc: dict) -> dict:
    """Build completed 1m bar dict from accumulator snapshot. Thread-safe: no I/O."""
    fb = acc["fb_end"] - acc["fb_start"]
    fs = acc["fs_end"] - acc["fs_start"]
    return {
        "ticker": ticker,
        "bar_time": acc["minute_key"],
        "open": acc["open"],
        "high": acc["high"],
        "low": acc["low"],
        "close": acc["close"],
        "volume": acc["volume"],
        "bu": acc["bu"],
        "sd": acc["sd"],
        "fb": max(0, fb),
        "fs": max(0, fs),
        "fn": fb - fs,
    }


def _accumulate_tick(d: dict) -> tuple[Optional[dict], Optional[dict]]:
    """
    Process one RealTimeData.to_dict() tick.

    Returns (completed_bar, partial_bar):
      - completed_bar: full 1m bar from the *previous* minute (ready for DB + M1)
      - partial_bar:   current running bar (for mid-minute M1 early detection)

    Fix 4: fb/fs delta for the first tick of each minute now uses the previous
    minute's final fb_end as fb_start, so no foreign volume is lost at boundaries.
    """
    ticker = (d.get("Ticker") or "").upper()
    if not ticker:
        return None, None

    ts_raw = d.get("Timestamp", "")
    if not ts_raw:
        return None, None

    try:
        # Timestamp arrives as "2026-03-25T09:35:33" (ICT, no tz suffix)
        ts_ict = datetime.fromisoformat(ts_raw[:19]).replace(tzinfo=_ICT)
    except Exception:
        return None, None

    ts_utc = ts_ict.astimezone(timezone.utc)
    minute_key = ts_utc.replace(second=0, microsecond=0)

    match_vol = int(d.get("MatchVolume") or 0)
    bu = int(d.get("Bu") or 0)
    sd = int(d.get("Sd") or 0)
    close = float(d.get("Close") or 0)
    fb_total = int(d.get("ForeignBuyVolumeTotal") or 0)
    fs_total = int(d.get("ForeignSellVolumeTotal") or 0)

    completed: Optional[dict] = None

    with _tick_lock:
        acc = _tick_bars.get(ticker)

        if acc is None or acc["minute_key"] != minute_key:
            # Minute boundary: emit completed bar from previous minute
            if acc is not None and acc["volume"] > 0:
                completed = _emit_bar(ticker, acc)

            # Fix 4: carry forward prev minute's fb_end as fb_start for new minute,
            # so the first tick's foreign-flow contribution is captured correctly.
            fb_start = acc["fb_end"] if acc is not None else fb_total
            fs_start = acc["fs_end"] if acc is not None else fs_total

            _tick_bars[ticker] = {
                "minute_key": minute_key,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": match_vol,
                "bu": bu,
                "sd": sd,
                "fb_start": fb_start,
                "fs_start": fs_start,
                "fb_end": fb_total,
                "fs_end": fs_total,
            }
        else:
            acc["high"] = max(acc["high"], close)
            acc["low"] = min(acc["low"], close)
            acc["close"] = close
            acc["volume"] += match_vol
            acc["bu"] += bu
            acc["sd"] += sd
            acc["fb_end"] = fb_total
            acc["fs_end"] = fs_total

        # Build partial bar (exact tick timestamp for elapsed_seconds projection in M1)
        current = _tick_bars.get(ticker)
        partial: Optional[dict] = None
        if current and current["volume"] > 0:
            fb = current["fb_end"] - current["fb_start"]
            fs = current["fs_end"] - current["fs_start"]
            partial = {
                "ticker": ticker,
                "bar_time": ts_utc,          # exact time → M1 uses .second for projection
                "open": current["open"],
                "high": current["high"],
                "low": current["low"],
                "close": current["close"],
                "volume": current["volume"],
                "bu": current["bu"],
                "sd": current["sd"],
                "fb": max(0, fb),
                "fs": max(0, fs),
                "fn": fb - fs,
            }

    return completed, partial


def _on_tick_raw(data):
    """Trading_Data_Stream callback — called from stream thread per matched trade."""
    global _session_confirmed

    # Race gate: _close_event() sets _shutting_down=True before flushing _tick_bars.
    # Returning here ensures no new ticks mutate the accumulator during shutdown/flush.
    if _shutting_down:
        return

    try:
        d = data.to_dict()
    except Exception:
        return

    ticker = (d.get("Ticker") or "").upper()
    if ticker not in _WATCHLIST_SET:
        return

    # Mark session as confirmed on the first valid watchlist tick.
    if not _session_confirmed:
        _session_confirmed = True

    completed, partial = _accumulate_tick(d)

    if completed is not None:
        _set_last_bar_time(completed["bar_time"])
        if _loop is not None:
            asyncio.run_coroutine_threadsafe(_process_bar(completed), _loop)

    if partial is not None:
        _set_last_bar_time(partial["bar_time"])
        now_t = _mono()
        if now_t - _last_m1_check.get(ticker, 0) >= _M1_CHECK_INTERVAL:
            _last_m1_check[ticker] = now_t
            if _loop is not None:
                asyncio.run_coroutine_threadsafe(_process_partial(partial), _loop)


# ── Bar processing ─────────────────────────────────────────────────────────

async def _process_bar(bar: dict):
    """Completed 1m bar: save to DB and run M1 alert engine."""
    try:
        ticker = bar["ticker"]
        if ticker not in _WATCHLIST_SET:
            return
        await _save_bar(bar)
        await alert_engine_m1.process(bar)  # is_partial=False → confirm accumulator runs
    except Exception as e:
        logger.error(f"_process_bar error for {bar.get('ticker')}: {e}", exc_info=True)


async def _process_partial(bar: dict):
    """Partial (mid-minute) bar: M1 early detection only, no DB write.

    Fix 2: passes is_partial=True so _check_confirmations() is skipped.
    Without this, each 15-s partial call would add the cumulative minute volume
    to the pending confirm accumulator, inflating the 15-min ratio 2-4x.
    """
    try:
        if bar.get("ticker") not in _WATCHLIST_SET:
            return
        await alert_engine_m1.process(bar, is_partial=True)
    except Exception as e:
        logger.error(f"_process_partial error for {bar.get('ticker')}: {e}", exc_info=True)


async def _save_bar(bar: dict):
    """Upsert completed 1m bar into intraday_1m."""
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


# ── Fix 1: Minute-boundary flush timer ─────────────────────────────────────

async def _minute_flush_timer():
    """Flush bars that haven't been closed by a next tick.

    Guarantees that low-volume stocks and the final bar of each session
    are persisted to DB even when no subsequent tick arrives to trigger
    the normal minute-boundary emit in _accumulate_tick().
    Runs every 5 s; only flushes bars whose minute_key < current minute.
    """
    while True:
        await asyncio.sleep(5)

        if _shutting_down:
            continue

        now_utc = datetime.now(timezone.utc)
        current_minute = now_utc.replace(second=0, microsecond=0)

        to_flush = []
        with _tick_lock:
            for ticker in list(_tick_bars.keys()):
                acc = _tick_bars.get(ticker)
                if acc is not None and acc["minute_key"] < current_minute and acc["volume"] > 0:
                    to_flush.append(_emit_bar(ticker, acc))
                    del _tick_bars[ticker]

        for bar in to_flush:
            _set_last_bar_time(bar["bar_time"])
            await _process_bar(bar)
            logger.debug(f"Flush timer: emitted stale bar {bar['ticker']} {bar['bar_time']}")


# ── Fix 3: Flush in-progress bars + reset on disconnect ────────────────────

async def _flush_all_bars():
    """Flush ALL in-progress bars (including current minute's partial) to DB.

    Called by _close_event() before clearing state. Ensures that bars
    mid-accumulation during a proactive restart or manual stop are persisted,
    not silently dropped. Complement to _minute_flush_timer which only handles
    bars whose minute has already passed.
    """
    to_flush = []
    with _tick_lock:
        for ticker, acc in list(_tick_bars.items()):
            if acc["volume"] > 0:
                to_flush.append(_emit_bar(ticker, acc))
        _tick_bars.clear()
    for bar in to_flush:
        _set_last_bar_time(bar["bar_time"])
        await _process_bar(bar)
        logger.debug(f"Disconnect flush: {bar['ticker']} {bar['bar_time']}")


def _reset_tick_state():
    """Clear per-ticker accumulator and M1 check timestamps.

    Called after _flush_all_bars() so reconnects start with clean state.
    Without this, a partially accumulated bar from a crashed session
    can corrupt the first minute of the new session.
    """
    global _last_m1_check
    with _tick_lock:
        _tick_bars.clear()
    _last_m1_check.clear()


# ── Stream lifecycle ────────────────────────────────────────────────────────

def _stream_blocking():
    """Run blocking FiinQuantX tick stream. Called in thread executor."""
    global _stream_connected, _event, _client, _shutting_down
    import FiinQuantX as fq

    _stream_stop_event.clear()
    _shutting_down = False  # open tick gate for new session

    _client = fq.FiinSession(
        username=settings.FIINQUANT_USERNAME,
        password=settings.FIINQUANT_PASSWORD,
    ).login()

    _event = _client.Trading_Data_Stream(
        tickers=list(_ACTIVE_TICKERS),
        callback=_on_tick_raw,
    )
    _stream_connected = True
    logger.info(f"FiinQuantX tick stream started for {len(_ACTIVE_TICKERS)} tickers")
    _event.start()

    # Fix 5: block on our own threading.Event instead of polling _event._stop
    # (private attribute; not a stable library contract)
    _stream_stop_event.wait()
    _stream_connected = False
    logger.info("FiinQuantX tick stream ended")


async def _close_event():
    """Stop tick stream without blocking event loop."""
    global _event, _client, _stream_connected, _shutting_down, _session_confirmed
    _stream_connected = False
    _session_confirmed = False  # reset: next session must re-confirm with a live tick
    _shutting_down = True       # gate tick callback before flushing (eliminates race)
    _stream_stop_event.set()    # unblock _stream_blocking()
    await _flush_all_bars()    # persist current minute's data before clearing
    _reset_tick_state()        # clear stale accumulator state across reconnects
    if _event:
        event_ref, _event = _event, None
        try:
            await asyncio.get_event_loop().run_in_executor(None, event_ref.stop)
            logger.info("FiinQuantX tick stream stopped successfully")
        except Exception as e:
            logger.error(f"FiinQuantX event.stop() failed: {e}")
    _client = None


async def _watchdog():
    """Restart stream if no ticks received during trading hours for too long.

    Handles two cases:
    - Cold start / silent death: connected but never received first bar.
      Uses _CONNECT_TIMEOUT_SECS (120 s) grace period, then forces reconnect.
    - Stale stream: received bars previously but nothing for _STALE_MINUTES (10 min).
    """
    from app.utils.trading_hours import is_trading_day
    from app.utils.timezone import to_ict
    from datetime import time as dtime

    while True:
        await asyncio.sleep(60)

        if not _stream_connected:
            continue

        now_utc = datetime.now(timezone.utc)
        now_ict = to_ict(now_utc)

        if not (dtime(9, 0) <= now_ict.time() <= dtime(15, 10)):
            continue
        if not is_trading_day(now_ict.date()):
            continue

        # Fix 2: cold start — connected but never received a bar.
        # Without this check, watchdog would never trigger on a silently dead stream.
        if _last_bar_time is None:
            if _startup_at and (now_utc - _startup_at).total_seconds() > _CONNECT_TIMEOUT_SECS:
                logger.warning(
                    "No tick received within startup timeout — stream may be silently dead, forcing reconnect"
                )
                await _close_event()
            continue

        age_min = (now_utc - _last_bar_time).total_seconds() / 60
        if age_min > _STALE_MINUTES:
            logger.warning(
                f"Stream stale: no ticks for {age_min:.1f} min — forcing reconnect"
            )
            await _close_event()


async def _proactive_restart_timer():
    """Close stream before 1-hour JWT expires."""
    await asyncio.sleep(_PROACTIVE_RESTART_SECS)
    if _stream_connected:
        logger.info("Proactive restart: closing tick stream before JWT expires (55 min timer)")
        await _close_event()


async def start():
    """Start tick stream with infinite retry. Non-blocking (runs in thread executor)."""
    global _stream_connected, _loop, _watchdog_task, _flush_task, _startup_at
    _loop = asyncio.get_running_loop()
    _startup_at = datetime.now(timezone.utc)

    _watchdog_task = asyncio.create_task(_watchdog())
    _flush_task = asyncio.create_task(_minute_flush_timer())  # Fix 1

    attempt = 0
    while True:
        attempt += 1
        run_start = _loop.time()
        refresh_task = asyncio.create_task(_proactive_restart_timer())
        try:
            await _refresh_active_tickers()
            logger.info(f"FiinQuantX tick stream starting (attempt {attempt})")
            await _loop.run_in_executor(None, _stream_blocking)
            logger.info("FiinQuantX tick stream disconnected")
        except ImportError:
            logger.warning("FiinQuantX not installed — tick stream disabled (dev mode)")
            await _close_event()
            refresh_task.cancel()
            _watchdog_task.cancel()
            _flush_task.cancel()
            return
        except asyncio.CancelledError:
            logger.info("Stream task cancelled")
            await _close_event()
            refresh_task.cancel()
            _watchdog_task.cancel()
            _flush_task.cancel()
            raise
        except Exception as e:
            logger.error(f"Stream error (attempt {attempt}): {e}", exc_info=True)
        finally:
            await _close_event()
            refresh_task.cancel()

        elapsed = _loop.time() - run_start
        if elapsed > _PROACTIVE_RESTART_SECS - 60:
            attempt = 0
            wait = _PROACTIVE_RESTART_WAIT
        elif elapsed > 300:
            attempt = 0
            wait = BACKOFF_BASE
        else:
            wait = min(BACKOFF_BASE * (2 ** min(attempt - 1, 6)), BACKOFF_MAX)

        logger.info(f"Reconnecting in {wait}s (session ran {elapsed:.0f}s)...")
        await asyncio.sleep(wait)


async def stop():
    global _stream_connected, _watchdog_task, _flush_task
    await _close_event()
    if _watchdog_task:
        _watchdog_task.cancel()
        _watchdog_task = None
    if _flush_task:
        _flush_task.cancel()
        _flush_task = None
    logger.info("FiinQuantX tick stream stopped")
