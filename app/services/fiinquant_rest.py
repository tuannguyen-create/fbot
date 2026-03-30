"""FiinQuantX REST adapter — TradingView/GetStockChartData endpoint.

This path is used for daily historical backfill where the SDK path is either
too restrictive or inconsistent at larger universes. It returns OHLCV only
(no bu/sd/fb/fs/fn), so SDK remains valuable as a per-ticker fallback.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime

import requests as _requests

from app.config import settings

logger = logging.getLogger(__name__)

_CHART_URL = "https://fiinquant.fiintrade.vn/TradingView/GetStockChartData"
_CONCURRENCY = 10
_TIMEOUT = 15
_MAX_RETRIES = 3
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


@dataclass
class DailyRestFetchResult:
    bars: list[dict]
    tickers_with_rows: list[str]
    empty_tickers: list[str]
    failed_tickers: list[str]


# ── Auth ──────────────────────────────────────────────────────────────────

def _get_token() -> str:
    """Login via FiinQuantX SDK and return JWT access_token."""
    import FiinQuantX as fq

    client = fq.FiinSession(
        username=settings.FIINQUANT_USERNAME,
        password=settings.FIINQUANT_PASSWORD,
    ).login()
    return client.access_token


# ── Parsing ───────────────────────────────────────────────────────────────

def _parse_rest_bar(ticker: str, item: dict) -> dict | None:
    """Parse REST response item → daily_ohlcv-compatible dict.

    REST fields: t (timestamp), o (open), h (high), l (low), c (close),
    v (volume), val (turnover).  No flow fields (bu/sd/fb/fs/fn).
    """
    try:
        t = item.get("t")
        if not t:
            return None
        bar_date = datetime.fromisoformat(t[:10]).date()

        def _float(v):
            try:
                return float(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        def _int(v):
            try:
                return int(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        return {
            "ticker": ticker.upper(),
            "date": bar_date,
            "open": _float(item.get("o")),
            "high": _float(item.get("h")),
            "low": _float(item.get("l")),
            "close": _float(item.get("c")),
            "volume": _int(item.get("v")),
            # REST endpoint doesn't provide flow fields
            "bu": None,
            "sd": None,
            "fb": None,
            "fs": None,
            "fn": None,
        }
    except Exception as e:
        logger.warning(f"Failed to parse REST bar for {ticker}: {e}")
        return None


# ── Per-ticker fetch ──────────────────────────────────────────────────────

def _fetch_one_ticker(
    headers: dict[str, str],
    ticker: str,
    page_size: int,
) -> tuple[str, list[dict]]:
    """Fetch daily bars for a single ticker via REST.

    Returns `(status, bars)` where status is one of:
    - `ok`: bars returned
    - `empty`: request succeeded but no usable rows
    - `failed`: transport/server error after retries
    """
    params = {
        "Code": ticker,
        "Type": "Stock",
        "Frequency": "Daily",
        "PageSize": str(page_size),
    }

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = _requests.get(
                _CHART_URL,
                params=params,
                headers=headers,
                timeout=_TIMEOUT,
            )
            if r.status_code != 200:
                if r.status_code in _RETRYABLE_HTTP and attempt < _MAX_RETRIES:
                    time.sleep(0.25 * attempt)
                    continue
                logger.debug(f"REST {ticker}: HTTP {r.status_code}")
                return "failed", []

            data = r.json()
            if data.get("status") == "Failed":
                logger.debug(f"REST {ticker}: {data.get('errors', [])}")
                return "empty", []

            bars = []
            for item in data.get("items", []):
                bar = _parse_rest_bar(ticker, item)
                if bar and bar["volume"] and bar["volume"] > 0:
                    bars.append(bar)
            return ("ok", bars) if bars else ("empty", [])
        except Exception as e:
            if attempt < _MAX_RETRIES:
                time.sleep(0.25 * attempt)
                continue
            logger.warning(f"REST {ticker} error: {e}")
            return "failed", []

    return "failed", []


# ── Public API ────────────────────────────────────────────────────────────

def fetch_daily_bars_with_status_blocking(
    tickers: list[str],
    days: int = 25,
) -> DailyRestFetchResult:
    """Fetch daily OHLCV via REST for all tickers and report coverage details."""
    try:
        token = _get_token()
    except ImportError:
        logger.warning("FiinQuantX not installed — REST daily fetch disabled")
        return DailyRestFetchResult([], [], list(tickers), [])
    except Exception as e:
        logger.error(f"FiinQuantX login failed for REST adapter: {e}")
        return DailyRestFetchResult([], [], list(tickers), [])

    headers = {"Authorization": f"Bearer {token}"}

    collected: list[dict] = []
    tickers_with_rows: list[str] = []
    empty_tickers: list[str] = []
    failed_tickers: list[str] = []

    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
        futures = {
            executor.submit(_fetch_one_ticker, headers, ticker, days): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                status, bars = future.result()
                if status == "ok":
                    collected.extend(bars)
                    tickers_with_rows.append(ticker)
                elif status == "empty":
                    empty_tickers.append(ticker)
                else:
                    failed_tickers.append(ticker)
            except Exception as e:
                logger.warning(f"REST {ticker} exception: {e}")
                failed_tickers.append(ticker)

    logger.info(
        f"REST daily fetch: {len(collected)} bars from {len(tickers_with_rows)}/{len(tickers)} "
        f"tickers ({len(empty_tickers)} empty, {len(failed_tickers)} failed)"
    )
    return DailyRestFetchResult(
        bars=collected,
        tickers_with_rows=tickers_with_rows,
        empty_tickers=empty_tickers,
        failed_tickers=failed_tickers,
    )


def fetch_daily_bars_blocking(tickers: list[str], days: int = 25) -> list[dict]:
    """Compatibility wrapper returning only the bars payload."""
    return fetch_daily_bars_with_status_blocking(tickers, days).bars
