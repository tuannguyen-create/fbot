"""FiinQuantX REST adapter — TradingView endpoints.

This path is used for daily historical backfill where the SDK path is either
too restrictive or inconsistent at larger universes. It returns OHLCV only
(no bu/sd/fb/fs/fn), so SDK remains valuable as a per-ticker fallback.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from datetime import date as date_type
from zoneinfo import ZoneInfo

import requests as _requests

from app.config import settings

logger = logging.getLogger(__name__)

_CHART_URL = "https://fiinquant.fiintrade.vn/TradingView/GetStockChartData"
_BUSD_URL = "https://fiinquant.fiintrade.vn/TradingView/GetIndicatorBuSd"
_FOREIGN_URL = "https://fiinquant.fiintrade.vn/TradingView/GetIndicatorForeign"
_CONCURRENCY = 10
_TIMEOUT = 15
_MAX_RETRIES = 3
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}
_ICT = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass
class DailyRestFetchResult:
    bars: list[dict]
    tickers_with_rows: list[str]
    empty_tickers: list[str]
    failed_tickers: list[str]


@dataclass
class IntradayRestFetchResult:
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


def _request_json(
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
) -> tuple[str, dict]:
    """Perform a REST call with retries.

    Returns `(status, payload)` where status is:
    - `ok`: HTTP 200 with parsed JSON payload
    - `empty`: non-retryable provider rejection (e.g. TickerLimitFailed)
    - `failed`: transport/server error after retries
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = _requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
            if response.status_code == 200:
                return "ok", response.json()
            if response.status_code in _RETRYABLE_HTTP and attempt < _MAX_RETRIES:
                time.sleep(0.25 * attempt)
                continue
            try:
                payload = response.json()
            except Exception:
                payload = {}
            if response.status_code in {400, 401, 403, 404}:
                return "empty", payload
            return "failed", payload
        except Exception as exc:
            if attempt < _MAX_RETRIES:
                time.sleep(0.25 * attempt)
                continue
            logger.warning(f"REST request error {url}: {exc}")
            return "failed", {}
    return "failed", {}


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


def _parse_rest_intraday_bar(
    ticker: str,
    chart_item: dict,
    busd_item: dict | None = None,
    foreign_item: dict | None = None,
) -> dict | None:
    """Parse EachMinute REST payloads into intraday_1m-compatible rows."""
    try:
        t = chart_item.get("t")
        if not t:
            return None
        bar_ict = datetime.fromisoformat(t[:19]).replace(tzinfo=_ICT)

        def _float(value):
            try:
                return float(value) if value is not None else None
            except (ValueError, TypeError):
                return None

        def _int(value):
            try:
                return int(float(value)) if value is not None else 0
            except (ValueError, TypeError):
                return 0

        return {
            "ticker": ticker.upper(),
            "bar_time": bar_ict.astimezone(timezone.utc),
            "open": _float(chart_item.get("o")),
            "high": _float(chart_item.get("h")),
            "low": _float(chart_item.get("l")),
            "close": _float(chart_item.get("c")),
            "volume": _int(chart_item.get("v")),
            "bu": _int((busd_item or {}).get("b")),
            "sd": _int((busd_item or {}).get("s")),
            "fb": _int((foreign_item or {}).get("fb")),
            "fs": _int((foreign_item or {}).get("fs")),
            "fn": _int((foreign_item or {}).get("fn")),
        }
    except Exception as e:
        logger.warning(f"Failed to parse intraday REST bar for {ticker}: {e}")
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
    status, data = _request_json(_CHART_URL, headers, params)
    if status != "ok":
        return status, []
    if data.get("status") == "Failed":
        logger.debug(f"REST {ticker}: {data.get('errors', [])}")
        return "empty", []

    bars = []
    for item in data.get("items", []):
        bar = _parse_rest_bar(ticker, item)
        if bar and bar["volume"] and bar["volume"] > 0:
            bars.append(bar)
    return ("ok", bars) if bars else ("empty", [])


def _fetch_one_intraday_ticker(
    headers: dict[str, str],
    ticker: str,
    from_date: date_type,
    to_date: date_type,
    page_size: int,
) -> tuple[str, list[dict]]:
    """Fetch 1m bars for a single ticker via REST + indicator endpoints."""
    base_params = {
        "Code": ticker,
        "Type": "Stock",
        "Frequency": "EachMinute",
        "From": from_date.isoformat(),
        "To": to_date.isoformat(),
        "PageSize": str(page_size),
    }

    chart_status, chart_data = _request_json(_CHART_URL, headers, base_params)
    if chart_status != "ok":
        return chart_status, []
    if chart_data.get("status") == "Failed":
        logger.debug(f"REST intraday {ticker}: {chart_data.get('errors', [])}")
        return "empty", []

    chart_items = chart_data.get("items", [])
    if not chart_items:
        return "empty", []

    busd_status, busd_data = _request_json(_BUSD_URL, headers, base_params)
    foreign_status, foreign_data = _request_json(_FOREIGN_URL, headers, base_params)

    busd_map = {
        item.get("t"): item
        for item in (busd_data.get("items", []) if busd_status == "ok" else [])
        if item.get("t")
    }
    foreign_map = {
        item.get("t"): item
        for item in (foreign_data.get("items", []) if foreign_status == "ok" else [])
        if item.get("t")
    }

    bars = []
    for chart_item in chart_items:
        ts = chart_item.get("t")
        bar = _parse_rest_intraday_bar(
            ticker,
            chart_item,
            busd_map.get(ts),
            foreign_map.get(ts),
        )
        if bar and bar["volume"] > 0:
            bars.append(bar)

    return ("ok", bars) if bars else ("empty", [])


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


def fetch_intraday_bars_with_status_blocking(
    tickers: list[str],
    from_date: date_type,
    to_date: date_type,
) -> IntradayRestFetchResult:
    """Fetch 1m OHLCV + flow fields via REST for all tickers and report coverage."""
    try:
        token = _get_token()
    except ImportError:
        logger.warning("FiinQuantX not installed — REST intraday fetch disabled")
        return IntradayRestFetchResult([], [], list(tickers), [])
    except Exception as e:
        logger.error(f"FiinQuantX login failed for REST intraday adapter: {e}")
        return IntradayRestFetchResult([], [], list(tickers), [])

    headers = {"Authorization": f"Bearer {token}"}
    calendar_days = max((to_date - from_date).days + 1, 1)
    page_size = min(max(calendar_days * 300, 2000), 10000)

    collected: list[dict] = []
    tickers_with_rows: list[str] = []
    empty_tickers: list[str] = []
    failed_tickers: list[str] = []

    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
        futures = {
            executor.submit(_fetch_one_intraday_ticker, headers, ticker, from_date, to_date, page_size): ticker
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
                logger.warning(f"REST intraday {ticker} exception: {e}")
                failed_tickers.append(ticker)

    logger.info(
        f"REST intraday fetch: {len(collected)} bars from {len(tickers_with_rows)}/{len(tickers)} "
        f"tickers ({len(empty_tickers)} empty, {len(failed_tickers)} failed) "
        f"for {from_date} → {to_date}"
    )
    return IntradayRestFetchResult(
        bars=collected,
        tickers_with_rows=tickers_with_rows,
        empty_tickers=empty_tickers,
        failed_tickers=failed_tickers,
    )


def fetch_intraday_bars_blocking(
    tickers: list[str],
    from_date: date_type,
    to_date: date_type,
) -> list[dict]:
    """Compatibility wrapper returning only the intraday bars payload."""
    return fetch_intraday_bars_with_status_blocking(tickers, from_date, to_date).bars
