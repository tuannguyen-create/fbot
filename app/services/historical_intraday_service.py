"""Historical intraday 1-minute bar backfill — seeds intraday_1m from FiinQuantX.

Flow:
  1. _fetch_1m_blocking()   — calls FiinQuantX Fetch_Trading_Data(by='1m') in thread executor
  2. _upsert_intraday()     — bulk upsert into intraday_1m (ON CONFLICT DO UPDATE)
  3. baseline_service.rebuild_all(force=True) — recompute baselines from fresh data

FiinQuantX SDK supports historical 1m data, but availability depends on the
subscription plan (ticker count, history depth). The app degrades gracefully:
if the current plan cannot fulfil the request, 0 bars are returned and M1
bootstrap is skipped with a clear log message.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings
from app.services import baseline_service, universe_service

logger = logging.getLogger(__name__)

_pool = None
_ICT = ZoneInfo("Asia/Ho_Chi_Minh")
_FETCH_BATCH_SIZE = 100
_UPSERT_BATCH_SIZE = 5000
# FiinQuantX 1m data: max 31 calendar days per request.
# from_date & to_date are inclusive, so (to - from).days must be ≤ 30.
# Use 29 so each chunk spans exactly 30 inclusive days at most.
_MAX_FIIN_1M_CALENDAR_DAYS = 29


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
    """Fetch historical 1m bars from FiinQuantX. Blocking — run in executor.

    Tries from_date/to_date first; if 0 bars returned, falls back to
    period-based fetch (same approach that works for daily OHLCV).
    """
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

        # Primary: date-range fetch
        calendar_days = (to_date - from_date).days
        logger.info(
            f"Historical 1m fetch attempt: {len(tickers)} tickers, "
            f"{from_date} → {to_date} ({calendar_days} cal days)"
        )
        try:
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
        except Exception as e:
            logger.warning(f"Historical 1m date-range fetch failed: {e}")

        # Fallback: period-based fetch (same style as daily OHLCV which works)
        if not collected:
            logger.info(
                f"Historical 1m date-range returned 0 bars — "
                f"trying period={calendar_days} fallback"
            )
            try:
                event2 = client.Fetch_Trading_Data(
                    realtime=False,
                    tickers=tickers,
                    fields=["open", "high", "low", "close", "volume", "bu", "sd", "fb", "fs", "fn"],
                    by="1m",
                    period=calendar_days,
                    callback=_on_bar,
                )
                event2.get_data()
            except Exception as e:
                logger.warning(f"Historical 1m period-based fallback also failed: {e}")

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
        for i in range(0, len(rows), _UPSERT_BATCH_SIZE):
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
                rows[i:i + _UPSERT_BATCH_SIZE],
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
    tickers = await universe_service.get_active_tickers()
    if not tickers:
        return False
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
            tickers,
        )
    threshold = max(len(tickers) // 2, 1)
    return (covered or 0) < threshold


async def backfill_intraday(days: int | None = None) -> int:
    """Fetch and persist recent 1m OHLCV from FiinQuantX within plan retention window.

    Returns the total number of bars upserted (0 if plan doesn't allow 1m history).

    The effective window is clamped to FIINQUANT_INTRADAY_HISTORY_DAYS calendar
    days — the provider's retention limit. Requesting older data would fail even
    if chunked into smaller requests, because the provider rejects any from_date
    beyond the retention boundary.

    Ticker batches are capped to FIINQUANT_TICKER_LIMIT to respect the plan.
    Rebuilds baselines when done.
    """
    retention_days = settings.FIINQUANT_INTRADAY_HISTORY_DAYS
    to_date = date.today() - timedelta(days=1)  # up to yesterday
    # Clamp to provider retention: oldest_allowed is inclusive
    oldest_allowed = to_date - timedelta(days=retention_days - 1)

    if days is not None:
        requested_from = to_date - timedelta(days=days + 10)  # calendar buffer
        from_date = max(requested_from, oldest_allowed)
    else:
        from_date = oldest_allowed

    effective_cal_days = (to_date - from_date).days
    # Estimate trading sessions (~5 trading days per 7 calendar days)
    est_trading_sessions = effective_cal_days * 5 // 7

    logger.info(
        f"M1 intraday backfill plan: provider retention={retention_days} cal days, "
        f"effective window={from_date} → {to_date} ({effective_cal_days} cal days, "
        f"~{est_trading_sessions} trading sessions)"
    )

    # Build date chunks of ≤ _MAX_FIIN_1M_CALENDAR_DAYS each
    # All chunks are within retention window, so none will be rejected
    date_chunks: list[tuple[date, date]] = []
    chunk_end = to_date
    while chunk_end > from_date:
        chunk_start = max(from_date, chunk_end - timedelta(days=_MAX_FIIN_1M_CALENDAR_DAYS))
        date_chunks.append((chunk_start, chunk_end))
        chunk_end = chunk_start - timedelta(days=1)

    tickers = await universe_service.get_active_tickers(force_refresh=True)
    if not tickers:
        logger.warning("Skipping 1m intraday backfill: active universe is empty")
        return 0

    ticker_limit = settings.FIINQUANT_TICKER_LIMIT
    batch_size = min(_FETCH_BATCH_SIZE, ticker_limit)
    if len(tickers) > ticker_limit:
        logger.warning(
            f"Active universe ({len(tickers)}) exceeds FiinQuantX ticker limit "
            f"({ticker_limit}) — backfilling first {ticker_limit} tickers only"
        )
        tickers = tickers[:ticker_limit]

    logger.info(
        f"Starting 1m intraday backfill: {from_date} → {to_date} "
        f"({len(date_chunks)} chunk(s)) for {len(tickers)} tickers"
    )
    loop = asyncio.get_running_loop()
    total_count = 0
    for chunk_start, chunk_end_dt in date_chunks:
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i + batch_size]
            bars = await loop.run_in_executor(
                None,
                lambda b=batch, s=chunk_start, e=chunk_end_dt: _fetch_1m_blocking(b, s, e),
            )
            total_count += await _upsert_intraday(bars)

    if total_count > 0:
        await baseline_service.rebuild_all(force=True)
        logger.info(f"Intraday backfill complete: {total_count} bars, baselines rebuilt")
    else:
        logger.warning(
            f"Intraday backfill: 0 bars fetched — current FiinQuantX plan may not "
            f"support 1m historical data for {ticker_limit} tickers / "
            f"{effective_cal_days} calendar days"
        )
    return total_count
