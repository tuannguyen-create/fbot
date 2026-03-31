from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import notification


@pytest.fixture(autouse=True)
def reset_notification_state():
    notification._pool = None
    notification._resend = None
    notification._redis = None
    yield
    notification._pool = None
    notification._resend = None
    notification._redis = None


def test_should_send_m1_fired_telegram_filters_tiny_volume():
    alert = {
        "status": "fired",
        "ticker": "ANT",
        "ratio_5d": 12.0,
        "volume": 100,
        "quality_grade": "A",
    }
    assert notification.should_send_m1_fired_telegram(alert) is False


def test_should_send_m1_fired_telegram_accepts_strong_a_grade():
    alert = {
        "status": "fired",
        "ticker": "VNM",
        "ratio_5d": 5.4,
        "volume": 80_000,
        "quality_grade": "A",
    }
    assert notification.should_send_m1_fired_telegram(alert) is True


def test_should_send_m1_fired_telegram_accepts_extreme_move():
    alert = {
        "status": "fired",
        "ticker": "SHB",
        "ratio_5d": 12.0,
        "volume": 900_000,
        "quality_grade": "C",
    }
    assert notification.should_send_m1_fired_telegram(alert) is True


def test_should_send_m1_confirmation_telegram_only_for_confirmed():
    cancelled = {
        "status": "cancelled",
        "ratio_15m": 2.0,
        "volume": 50_000,
        "quality_grade": "A",
    }
    confirmed = {
        "status": "confirmed",
        "ratio_15m": 1.6,
        "volume": 25_000,
        "quality_grade": "B",
    }
    assert notification.should_send_m1_confirmation_telegram(cancelled) is False
    assert notification.should_send_m1_confirmation_telegram(confirmed) is True


@pytest.mark.asyncio
async def test_send_volume_alert_email_suppresses_low_signal_telegram(mock_pool, mock_redis):
    pool, conn = mock_pool
    notification.inject_deps(pool, mock_redis)
    conn.fetchrow = AsyncMock(return_value={
        "id": 1,
        "ticker": "ASM",
        "slot": 20,
        "fired_at": datetime(2026, 3, 31, 2, 20, tzinfo=timezone.utc),
        "bar_time": datetime(2026, 3, 31, 2, 20, tzinfo=timezone.utc),
        "status": "fired",
        "ratio_5d": 4.8,
        "volume": 1_500,
        "baseline_5d": 1_000,
        "bu_pct": 100.0,
        "foreign_net": 0,
        "in_magic_window": False,
        "quality_grade": "A",
    })

    with patch("app.services.notification._send_email", new=AsyncMock()) as mock_email, \
         patch("app.services.notification._send_telegram", new=AsyncMock()) as mock_tg:
        await notification.send_volume_alert_email(1)

    mock_email.assert_awaited_once()
    mock_tg.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_volume_alert_confirmation_sends_only_confirmed(mock_pool, mock_redis):
    pool, conn = mock_pool
    notification.inject_deps(pool, mock_redis)
    conn.fetchrow = AsyncMock(return_value={
        "id": 2,
        "ticker": "VCB",
        "slot": 20,
        "fired_at": datetime(2026, 3, 31, 2, 20, tzinfo=timezone.utc),
        "bar_time": datetime(2026, 3, 31, 2, 20, tzinfo=timezone.utc),
        "confirmed_at": datetime(2026, 3, 31, 2, 35, tzinfo=timezone.utc),
        "status": "confirmed",
        "ratio_15m": 1.8,
        "volume": 30_000,
        "quality_grade": "A",
        "features": {},
    })

    with patch("app.services.notification._send_telegram", new=AsyncMock()) as mock_tg:
        await notification.send_volume_alert_confirmation(2)

    mock_tg.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_volume_alert_confirmation_skips_cancelled(mock_pool, mock_redis):
    pool, conn = mock_pool
    notification.inject_deps(pool, mock_redis)
    conn.fetchrow = AsyncMock(return_value={
        "id": 3,
        "ticker": "MSN",
        "slot": 20,
        "fired_at": datetime(2026, 3, 31, 2, 20, tzinfo=timezone.utc),
        "bar_time": datetime(2026, 3, 31, 2, 20, tzinfo=timezone.utc),
        "confirmed_at": datetime(2026, 3, 31, 2, 35, tzinfo=timezone.utc),
        "status": "cancelled",
        "ratio_15m": 1.1,
        "volume": 60_000,
        "quality_grade": "A",
        "features": {},
    })

    with patch("app.services.notification._send_telegram", new=AsyncMock()) as mock_tg:
        await notification.send_volume_alert_confirmation(3)

    mock_tg.assert_not_awaited()
