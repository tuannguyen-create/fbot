from datetime import datetime, timezone
import pytz

ICT = pytz.timezone("Asia/Ho_Chi_Minh")
UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_ict(dt: datetime) -> datetime:
    """Convert any datetime to ICT (UTC+7)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ICT)


def to_utc(dt: datetime) -> datetime:
    """Convert ICT datetime to UTC."""
    if dt.tzinfo is None:
        dt = ICT.localize(dt)
    return dt.astimezone(UTC)


def format_ict(dt: datetime, fmt: str = "%d/%m/%Y %H:%M") -> str:
    return to_ict(dt).strftime(fmt)


def format_time_ict(dt: datetime) -> str:
    return to_ict(dt).strftime("%H:%M")
