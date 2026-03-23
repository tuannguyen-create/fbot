import asyncpg
import re
from app.config import settings
import logging

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _clean_dsn(url: str) -> str:
    """Strip sqlalchemy prefix and sslmode param (passed explicitly to asyncpg)."""
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    dsn = re.sub(r'[?&]sslmode=[^&]*', '', dsn).rstrip('?&')
    return dsn


async def init_pool() -> asyncpg.Pool:
    global _pool
    ssl = 'require' if settings.DATABASE_SSL else False
    _pool = await asyncpg.create_pool(
        dsn=_clean_dsn(settings.DATABASE_URL),
        min_size=2,
        max_size=10,
        command_timeout=30,
        ssl=ssl,
    )
    logger.info("PostgreSQL pool initialized")
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        logger.info("PostgreSQL pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized. Call init_pool() first.")
    return _pool


async def get_db() -> asyncpg.Pool:
    """FastAPI dependency."""
    return get_pool()
