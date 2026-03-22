"""Shared test fixtures."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock()))
    return pool, conn


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    redis.setex = AsyncMock()
    redis.pipeline = MagicMock(return_value=AsyncMock(
        hset=AsyncMock(),
        expire=AsyncMock(),
        execute=AsyncMock(return_value=[]),
    ))
    redis.ping = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def sample_bar():
    return {
        "ticker": "HPG",
        "bar_time": datetime(2026, 3, 18, 2, 15, 0, tzinfo=timezone.utc),  # 9:15 ICT
        "open": 25000.0,
        "high": 25200.0,
        "low": 24900.0,
        "close": 25100.0,
        "volume": 1_200_000,
        "bu": 800_000,
        "sd": 400_000,
        "fb": 100_000,
        "fs": 50_000,
        "fn": 50_000,
    }


@pytest.fixture
def baseline_5d_200k():
    return {"avg_5d": 200_000, "avg_20d": 180_000, "std_dev": 30_000}
