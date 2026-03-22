import redis.asyncio as aioredis
from app.config import settings
import logging

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis | None:
    global _redis
    if not settings.REDIS_URL:
        logger.info("REDIS_URL not set — running without Redis cache")
        return None
    _redis = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    # Test connection
    await _redis.ping()
    logger.info("Redis connected")
    return _redis


async def close_redis():
    global _redis
    if _redis:
        await _redis.aclose()
        logger.info("Redis closed")


def get_redis() -> aioredis.Redis | None:
    return _redis
