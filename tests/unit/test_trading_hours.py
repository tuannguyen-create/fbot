"""Unit tests for trading_hours utilities."""
import pytest
from datetime import time, date
from app.utils.trading_hours import (
    get_slot,
    slot_to_time_str,
    is_magic_window,
    is_trading_day,
    is_trading_hours,
    count_trading_days_between,
    add_trading_days,
)


class TestGetSlot:
    def test_market_open(self):
        assert get_slot(time(9, 0)) == 0

    def test_9_15(self):
        assert get_slot(time(9, 15)) == 15

    def test_11_00(self):
        assert get_slot(time(11, 0)) == 120

    def test_11_29_last_morning(self):
        assert get_slot(time(11, 29)) == 149

    def test_break_11_30(self):
        assert get_slot(time(11, 30)) is None

    def test_break_12_59(self):
        assert get_slot(time(12, 59)) is None

    def test_afternoon_open(self):
        assert get_slot(time(13, 0)) == 150

    def test_afternoon_14_00(self):
        assert get_slot(time(14, 0)) == 210

    def test_afternoon_close(self):
        assert get_slot(time(14, 29)) == 239

    def test_after_close(self):
        assert get_slot(time(14, 30)) is None

    def test_pre_market(self):
        assert get_slot(time(8, 59)) is None


class TestSlotToTimeStr:
    def test_slot_0(self):
        assert slot_to_time_str(0) == "09:00"

    def test_slot_15(self):
        assert slot_to_time_str(15) == "09:15"

    def test_slot_150(self):
        assert slot_to_time_str(150) == "13:00"

    def test_slot_239(self):
        assert slot_to_time_str(239) == "14:29"


class TestMagicWindow:
    def test_open_magic(self):
        assert is_magic_window(time(9, 0)) is True
        assert is_magic_window(time(9, 15)) is True
        assert is_magic_window(time(9, 29)) is True

    def test_open_magic_end(self):
        assert is_magic_window(time(9, 30)) is False

    def test_pre_break_magic(self):
        assert is_magic_window(time(11, 0)) is True
        assert is_magic_window(time(11, 15)) is True
        assert is_magic_window(time(11, 29)) is True

    def test_post_break_magic(self):
        assert is_magic_window(time(13, 0)) is True
        assert is_magic_window(time(13, 29)) is True

    def test_post_break_magic_end(self):
        assert is_magic_window(time(13, 30)) is False

    def test_normal_window(self):
        assert is_magic_window(time(10, 0)) is False
        assert is_magic_window(time(14, 0)) is False


class TestTradingDay:
    def test_normal_weekday_2026(self):
        assert is_trading_day(date(2026, 3, 18)) is True  # Wednesday

    def test_saturday(self):
        assert is_trading_day(date(2026, 3, 21)) is False  # Saturday

    def test_sunday(self):
        assert is_trading_day(date(2026, 3, 22)) is False  # Sunday

    def test_new_year(self):
        assert is_trading_day(date(2026, 1, 1)) is False

    def test_tet_holiday(self):
        assert is_trading_day(date(2026, 2, 16)) is False
        assert is_trading_day(date(2026, 2, 20)) is False

    def test_day_after_tet(self):
        assert is_trading_day(date(2026, 2, 23)) is True  # Monday after Tết


class TestCountTradingDays:
    def test_same_day(self):
        assert count_trading_days_between(date(2026, 3, 18), date(2026, 3, 18)) == 1

    def test_one_week(self):
        # Mon-Fri = 5 days
        assert count_trading_days_between(date(2026, 3, 16), date(2026, 3, 20)) == 5

    def test_skips_weekend(self):
        # Friday to Monday = 2 trading days (Fri + Mon)
        assert count_trading_days_between(date(2026, 3, 20), date(2026, 3, 23)) == 2


class TestAddTradingDays:
    def test_add_1_normal(self):
        # Tuesday + 1 = Wednesday
        result = add_trading_days(date(2026, 3, 17), 1)
        assert result == date(2026, 3, 18)

    def test_add_skips_weekend(self):
        # Friday + 1 = Monday
        result = add_trading_days(date(2026, 3, 20), 1)
        assert result == date(2026, 3, 23)

    def test_add_5(self):
        # Monday + 5 = next Monday
        result = add_trading_days(date(2026, 3, 16), 5)
        assert result == date(2026, 3, 23)
