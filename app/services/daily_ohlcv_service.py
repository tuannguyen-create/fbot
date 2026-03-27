"""Daily OHLCV service — backfill from FiinQuantX and aggregate from intraday_1m."""
import asyncio
import logging
from datetime import date, datetime, timezone

from app.config import settings
from app.services import universe_service

logger = logging.getLogger(__name__)

_pool = None
_FETCH_BATCH_SIZE = 200


def inject_deps(pool):
    global _pool
    _pool = pool


def _parse_daily_bar(raw: dict) -> dict | None:
    """Normalize a raw FiinQuantX callback dict → daily OHLCV dict."""
    try:
        ticker = raw.get("ticker") or raw.get("Symbol") or raw.get("symbol")
        bar_date_raw = raw.get("datetime") or raw.get("Date") or raw.get("date")
        if not ticker or bar_date_raw is None:
            return None

        if isinstance(bar_date_raw, str):
            bar_date = datetime.fromisoformat(bar_date_raw.replace("Z", "+00:00")).date()
        elif isinstance(bar_date_raw, datetime):
            bar_date = bar_date_raw.date()
        elif isinstance(bar_date_raw, date):
            bar_date = bar_date_raw
        else:
            bar_date = date.fromtimestamp(float(bar_date_raw))

        def _int(v):
            try:
                return int(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        def _float(v):
            try:
                return float(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        return {
            "ticker": str(ticker).upper(),
            "date": bar_date,
            "open": _float(raw.get("open") or raw.get("Open")),
            "high": _float(raw.get("high") or raw.get("High")),
            "low": _float(raw.get("low") or raw.get("Low")),
            "close": _float(raw.get("close") or raw.get("Close")),
            "volume": _int(raw.get("volume") or raw.get("Volume")),
            "bu": _int(raw.get("bu") or raw.get("BU")),
            "sd": _int(raw.get("sd") or raw.get("SD")),
            "fb": _int(raw.get("fb") or raw.get("FB")),
            "fs": _int(raw.get("fs") or raw.get("FS")),
            "fn": _int(raw.get("fn") or raw.get("FN")),
        }
    except Exception as e:
        logger.warning(f"Failed to parse daily bar: {e}")
        return None


def _fetch_historical_blocking(tickers: list[str], days: int = 25) -> list[dict]:
    """Fetch historical daily OHLCV bars from FiinQuantX (blocking, for thread executor)."""
    try:
        import FiinQuantX as fq

        client = fq.FiinSession(
            username=settings.FIINQUANT_USERNAME,
            password=settings.FIINQUANT_PASSWORD,
        ).login()

        collected: list[dict] = []

        def _on_bar(raw):
            bar = _parse_daily_bar(raw)
            if bar:
                collected.append(bar)

        event = client.Fetch_Trading_Data(
            realtime=False,
            tickers=tickers,
            fields=["open", "high", "low", "close", "volume", "bu", "sd", "fb", "fs", "fn"],
            by="1D",
            period=days,
            callback=_on_bar,
        )
        event.get_data()  # Blocking until all historical data returned
        logger.info(f"Historical daily OHLCV fetched: {len(collected)} bars for {len(tickers)} tickers")
        return collected
    except ImportError:
        logger.warning("FiinQuantX not installed — historical daily OHLCV fetch disabled")
        return []
    except Exception as e:
        logger.error(f"Historical daily OHLCV fetch error: {e}", exc_info=True)
        return []


async def _persist_bars(bars: list[dict]) -> int:
    """Upsert daily OHLCV bars into DB. Returns count upserted."""
    if not bars:
        return 0
    rows = [
        (
            b["ticker"], b["date"],
            b["open"], b["high"], b["low"], b["close"], b["volume"],
            b["bu"], b["sd"], b["fb"], b["fs"], b["fn"],
        )
        for b in bars
    ]
    async with _pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO daily_ohlcv
                (ticker, date, open, high, low, close, volume, bu, sd, fb, fs, fn)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (ticker, date) DO UPDATE SET
                open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                close=EXCLUDED.close, volume=EXCLUDED.volume,
                bu=EXCLUDED.bu, sd=EXCLUDED.sd, fb=EXCLUDED.fb,
                fs=EXCLUDED.fs, fn=EXCLUDED.fn
            """,
            rows,
        )
    logger.info(f"daily_ohlcv upserted {len(rows)} rows")
    return len(rows)


async def backfill_historical(days: int = 25):
    """
    Fetch and persist last N days of daily OHLCV from FiinQuantX.
    Called at startup to bootstrap M3 analysis.
    Falls back silently if FiinQuantX API is unavailable.
    """
    tickers = await universe_service.get_active_tickers(force_refresh=True)
    if not tickers:
        logger.warning("Skipping daily OHLCV backfill: active universe is empty")
        return

    logger.info(f"Starting daily OHLCV backfill (last {days} days) for {len(tickers)} active tickers")
    loop = asyncio.get_running_loop()
    total_count = 0
    for i in range(0, len(tickers), _FETCH_BATCH_SIZE):
        batch = tickers[i:i + _FETCH_BATCH_SIZE]
        bars = await loop.run_in_executor(
            None, lambda batch=batch: _fetch_historical_blocking(batch, days)
        )
        total_count += await _persist_bars(bars)
        logger.info(
            f"Daily OHLCV backfill batch {i // _FETCH_BATCH_SIZE + 1}: "
            f"{len(batch)} tickers, {len(bars)} rows"
        )
    logger.info(f"Daily OHLCV backfill complete: {total_count} rows upserted")


async def aggregate_today():
    """
    Aggregate intraday_1m bars for today → daily_ohlcv.
    Called by APScheduler at 15:10 ICT after market close.
    This is the primary daily refresh path — does not depend on FiinQuantX historical API.
    """
    today = date.today()
    logger.info(f"Aggregating daily_ohlcv from intraday_1m for {today}")
    async with _pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO daily_ohlcv
                (ticker, date, open, high, low, close, volume, bu, sd, fb, fs, fn)
            SELECT
                ticker,
                $1::date                                                        AS date,
                (ARRAY_AGG(open  ORDER BY bar_time))[1]                         AS open,
                MAX(high)                                                       AS high,
                MIN(low)                                                        AS low,
                (ARRAY_AGG(close ORDER BY bar_time DESC))[1]                    AS close,
                SUM(volume)                                                     AS volume,
                SUM(bu)                                                         AS bu,
                SUM(sd)                                                         AS sd,
                SUM(fb)                                                         AS fb,
                SUM(fs)                                                         AS fs,
                SUM(fn)                                                         AS fn
            FROM intraday_1m
            WHERE (bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = $1
              AND volume > 0
            GROUP BY ticker
            ON CONFLICT (ticker, date) DO UPDATE SET
                open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                close=EXCLUDED.close, volume=EXCLUDED.volume,
                bu=EXCLUDED.bu, sd=EXCLUDED.sd, fb=EXCLUDED.fb,
                fs=EXCLUDED.fs, fn=EXCLUDED.fn
            """,
            today,
        )
    logger.info(f"daily_ohlcv aggregated for {today}: {result}")
