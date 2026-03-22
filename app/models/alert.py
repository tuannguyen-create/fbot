from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class AlertSummary(BaseModel):
    id: int
    ticker: str
    fired_at: datetime
    slot: int
    volume: int
    ratio_5d: Optional[float]
    bu_pct: Optional[float]
    in_magic_window: bool
    status: str  # fired | confirmed | cancelled


class AlertDetail(AlertSummary):
    baseline_5d: Optional[int]
    foreign_net: Optional[int]
    confirmed_at: Optional[datetime]
    ratio_15m: Optional[float]
    email_sent: bool
    cycle_event_id: Optional[int]


class AlertListParams(BaseModel):
    ticker: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    status: Optional[str] = None
    magic_only: bool = False
    limit: int = 50
    offset: int = 0


class AlertTodaySummary(BaseModel):
    total: int
    confirmed: int
    fired: int
    cancelled: int
    by_ticker: dict[str, int]
