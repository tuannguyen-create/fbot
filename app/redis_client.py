import redis.asyncio as aioredis
from app.config import settings
import logging

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    global _redis
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


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _redis
