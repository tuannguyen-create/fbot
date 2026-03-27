"""Active ticker universe service.

Runtime modules should pull the active scan universe from the watchlist table,
not from the legacy 33-name settings.WATCHLIST constant.
"""
import logging
import time

from app.config import settings

logger = logging.getLogger(__name__)

_pool = None
_cached_tickers: tuple[str, ...] = tuple(settings.WATCHLIST)
_cache_loaded_at = 0.0
_CACHE_TTL_SECS = 60


def inject_deps(pool):
    global _pool
    _pool = pool


async def get_active_tickers(force_refresh: bool = False) -> list[str]:
    """Return active tickers from watchlist, with a short in-memory cache.

    Falls back to settings.WATCHLIST if the DB is unavailable or the watchlist
    table is empty. This keeps the app functional during migrations and local dev.
    """
    global _cached_tickers, _cache_loaded_at

    if (
        not force_refresh
        and _cached_tickers
        and (time.monotonic() - _cache_loaded_at) < _CACHE_TTL_SECS
    ):
        return list(_cached_tickers)

    if _pool is None:
        return list(_cached_tickers)

    try:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT ticker
                FROM watchlist
                WHERE active = TRUE
                ORDER BY in_vn30 DESC, ticker
                """
            )
        tickers = tuple(str(r["ticker"]).upper() for r in rows if r["ticker"])
        if tickers:
            _cached_tickers = tickers
            _cache_loaded_at = time.monotonic()
            return list(_cached_tickers)
        logger.warning("watchlist.active returned 0 rows — falling back to settings.WATCHLIST")
    except Exception as e:
        logger.warning(f"Failed to load active tickers from watchlist: {e}")

    _cached_tickers = tuple(settings.WATCHLIST)
    _cache_loaded_at = time.monotonic()
    return list(_cached_tickers)


def invalidate_cache():
    global _cache_loaded_at
    _cache_loaded_at = 0.0
