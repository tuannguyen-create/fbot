"""Historical intraday 1-minute bar backfill — seeds intraday_1m from FiinQuantX.

Flow:
  1. _fetch_1m_blocking()   — calls FiinQuantX Fetch_Trading_Data(by='1m') in thread executor
  2. _upsert_intraday()     — bulk upsert into intraday_1m (ON CONFLICT DO UPDATE)
  3. baseline_service.rebuild_all(force=True) — recompute baselines from fresh data

Triggered at startup when intraday_1m has < 5 trading days of data, so M1 baselines
are accurate from day 1 instead of degrading for the first week.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import baseline_service

logger = logging.getLogger(__name__)

_pool = None
_ICT = ZoneInfo("Asia/Ho_Chi_Minh")


def inject_deps(pool):
    global _pool
    _pool = pool


# ── Parsing ────────────────────────────────────────────────────────────────

def _parse_1m_bar(raw: dict) -> dict | None:
    """Normalize a FiinQuantX 1m-bar callback dict → intraday_1m row.

    FiinQuantX may use different key names depending on SDK version:
      - Timestamp / datetime / Date  (for bar time)
      - Ticker / ticker / Symbol     (for symbol)
    Time is assumed ICT (Asia/Ho_Chi_Minh) when no tz suffix is present.
    """
    try:
        ticker = (
            raw.get("Ticker") or raw.get("ticker") or
            raw.get("Symbol") or raw.get("symbol") or ""
        ).upper()
        if not ticker:
            return None

        ts_raw = (
            raw.get("Timestamp") or raw.get("timestamp") or
            raw.get("datetime") or raw.get("Date") or raw.get("date")
        )
        if not ts_raw:
            return None

        if isinstance(ts_raw, str):
            ts_ict = datetime.fromisoformat(ts_raw[:19]).replace(tzinfo=_ICT)
        elif isinstance(ts_raw, datetime):
            ts_ict = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=_ICT)
        else:
            return None

        bar_time_utc = ts_ict.astimezone(timezone.utc)

        def _int(v):
            try:
                return int(v) if v is not None else 0
            except (ValueError, TypeError):
                return 0

        def _float(v):
            try:
                return float(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        return {
            "ticker": ticker,
            "bar_time": bar_time_utc,
            "open":   _float(raw.get("open")   or raw.get("Open")),
            "high":   _float(raw.get("high")   or raw.get("High")),
            "low":    _float(raw.get("low")    or raw.get("Low")),
            "close":  _float(raw.get("close")  or raw.get("Close")),
            "volume": _int(raw.get("volume") or raw.get("Volume")),
            "bu": _int(raw.get("bu") or raw.get("BU")),
            "sd": _int(raw.get("sd") or raw.get("SD")),
            "fb": _int(raw.get("fb") or raw.get("FB")),
            "fs": _int(raw.get("fs") or raw.get("FS")),
            "fn": _int(raw.get("fn") or raw.get("FN")),
        }
    except Exception as e:
        logger.warning(f"Failed to parse 1m bar: {e}")
        return None


# ── FiinQuantX fetch (blocking, runs in thread executor) ───────────────────

def _fetch_1m_blocking(
    tickers: list[str],
    from_date: date,
    to_date: date,
) -> list[dict]:
    """Fetch historical 1m bars from FiinQuantX. Blocking — run in executor."""
    try:
        import FiinQuantX as fq

        client = fq.FiinSession(
            username=settings.FIINQUANT_USERNAME,
            password=settings.FIINQUANT_PASSWORD,
        ).login()

        collected: list[dict] = []

        def _on_bar(raw):
            bar = _parse_1m_bar(raw)
            if bar and bar["volume"] > 0:
                collected.append(bar)

        event = client.Fetch_Trading_Data(
            realtime=False,
            tickers=tickers,
            fields=["open", "high", "low", "close", "volume", "bu", "sd", "fb", "fs", "fn"],
            by="1m",
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
            callback=_on_bar,
        )
        event.get_data()
        logger.info(
            f"Historical 1m fetch: {len(collected)} bars "
            f"for {len(tickers)} tickers ({from_date} → {to_date})"
        )
        return collected

    except ImportError:
        logger.warning("FiinQuantX not installed — historical 1m fetch disabled")
        return []
    except Exception as e:
        logger.error(f"Historical 1m fetch error: {e}", exc_info=True)
        return []


# ── DB upsert ──────────────────────────────────────────────────────────────

async def _upsert_intraday(bars: list[dict]) -> int:
    """Bulk upsert 1m bars into intraday_1m. Returns count inserted."""
    if not bars:
        return 0
    rows = [
        (
            b["ticker"], b["bar_time"],
            b["open"], b["high"], b["low"], b["close"], b["volume"],
            b["bu"], b["sd"], b["fb"], b["fs"], b["fn"],
        )
        for b in bars
    ]
    async with _pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO intraday_1m
                (ticker, bar_time, open, high, low, close, volume, bu, sd, fb, fs, fn)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (ticker, bar_time) DO UPDATE SET
                open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                close=EXCLUDED.close, volume=EXCLUDED.volume,
                bu=EXCLUDED.bu, sd=EXCLUDED.sd, fb=EXCLUDED.fb,
                fs=EXCLUDED.fs, fn=EXCLUDED.fn
            """,
            rows,
        )
    logger.info(f"intraday_1m upserted {len(rows)} bars")
    return len(rows)


# ── Public API ─────────────────────────────────────────────────────────────

async def check_needs_backfill() -> bool:
    """Return True if fewer than half the watchlist tickers have ≥5 days of 1m data.

    Checks per-ticker coverage instead of global row count, so a situation
    where a few tickers have dense data but most are empty still triggers a
    backfill.
    """
    async with _pool.acquire() as conn:
        covered = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT ticker)
            FROM (
                SELECT ticker,
                       COUNT(DISTINCT DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh')) AS day_count
                FROM intraday_1m
                WHERE bar_time >= NOW() - INTERVAL '30 days'
                  AND ticker = ANY($1)
                GROUP BY ticker
                HAVING COUNT(DISTINCT DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh')) >= 5
            ) t
            """,
            settings.WATCHLIST,
        )
    threshold = max(len(settings.WATCHLIST) // 2, 1)
    return (covered or 0) < threshold


async def backfill_intraday(days: int = 25):
    """Fetch and persist last N trading days of 1m OHLCV from FiinQuantX.

    Seeds intraday_1m so volume_baselines reflect real market history rather
    than only the days the app has been running. Rebuilds baselines when done.

    days=25 fetches via calendar-day window of ~days+10 to account for
    weekends/holidays, giving at least 25 trading days of 1m bars.
    """
    to_date = date.today() - timedelta(days=1)        # up to yesterday
    from_date = to_date - timedelta(days=days + 10)   # calendar buffer

    logger.info(f"Starting 1m intraday backfill: {from_date} → {to_date}")
    loop = asyncio.get_running_loop()
    bars = await loop.run_in_executor(
        None, lambda: _fetch_1m_blocking(settings.WATCHLIST, from_date, to_date)
    )
    count = await _upsert_intraday(bars)

    if count > 0:
        await baseline_service.rebuild_all(force=True)
        logger.info(f"Intraday backfill complete: {count} bars, baselines rebuilt")
    else:
        logger.warning("Intraday backfill: 0 bars fetched — FiinQuantX may be unavailable")
