from datetime import time, date, datetime, timedelta
from typing import Optional

# Trading hours (ICT)
MARKET_OPEN = time(9, 0)
BREAK_START = time(11, 30)
BREAK_END = time(13, 0)
MARKET_CLOSE = time(14, 30)

# Slot mapping:
# 9:00-11:30 = 150 minutes = slots 0-149
# 13:00-14:30 = 90 minutes = slots 150-239
# Total meaningful slots: 240. Reserve 330 for future expansion.
TOTAL_SLOTS = 330

# Magic windows: lệch lối mòn highest probability
MAGIC_WINDOWS = [
    (time(9, 0),  time(9, 30)),
    (time(11, 0), time(11, 30)),
    (time(13, 0), time(13, 30)),
]

# Vietnam 2026 non-trading days
NON_TRADING_DAYS_2026: set[date] = {
    date(2026, 1, 1),
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20),
    date(2026, 4, 27),
    date(2026, 4, 30), date(2026, 5, 1),
    date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2),
}


def is_trading_day(d: date) -> bool:
    """Return True if d is a trading day (weekday + not holiday)."""
    return d.weekday() < 5 and d not in NON_TRADING_DAYS_2026


def get_slot(t: time) -> Optional[int]:
    """
    Convert ICT time → slot number.
    slot 0 = 9:00, slot 149 = 11:29, slot 150 = 13:00, slot 239 = 14:29
    Returns None outside trading hours or during break.
    """
    if MARKET_OPEN <= t < BREAK_START:
        h, m = t.hour, t.minute
        return (h * 60 + m) - (9 * 60)
    elif BREAK_END <= t < MARKET_CLOSE:
        h, m = t.hour, t.minute
        return 150 + (h * 60 + m) - (13 * 60)
    return None


def slot_to_time_str(slot: int) -> str:
    """Convert slot back to ICT time string HH:MM."""
    if slot < 150:
        total_min = 9 * 60 + slot
    else:
        total_min = 13 * 60 + (slot - 150)
    h = total_min // 60
    m = total_min % 60
    return f"{h:02d}:{m:02d}"


def is_magic_window(t: time) -> bool:
    """Return True if time is in a magic window."""
    return any(start <= t < end for start, end in MAGIC_WINDOWS)


def is_trading_hours(t: time) -> bool:
    """Return True if time is within trading hours (excludes break)."""
    if MARKET_OPEN <= t < BREAK_START:
        return True
    if BREAK_END <= t < MARKET_CLOSE:
        return True
    return False


def get_prev_trading_days(ref: date, n: int) -> list[date]:
    """Get last n trading days before (not including) ref date."""
    result = []
    d = ref - timedelta(days=1)
    while len(result) < n:
        if is_trading_day(d):
            result.append(d)
        d -= timedelta(days=1)
        if d < date(2020, 1, 1):
            break
    return list(reversed(result))


def count_trading_days_between(start: date, end: date) -> int:
    """Count trading days from start (inclusive) to end (inclusive)."""
    count = 0
    d = start
    while d <= end:
        if is_trading_day(d):
            count += 1
        d += timedelta(days=1)
    return count


def add_trading_days(start: date, n: int) -> date:
    """Add n trading days to start date."""
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if is_trading_day(d):
            added += 1
    return d
