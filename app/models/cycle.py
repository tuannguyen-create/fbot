from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional


class CycleSummary(BaseModel):
    id: int
    ticker: str
    breakout_date: date
    phase: str  # distributing | bottoming | done
    days_remaining: Optional[int]
    predicted_bottom_date: Optional[date]
    trading_days_elapsed: Optional[int]
    estimated_dist_days: Optional[int]


class CycleDetail(CycleSummary):
    peak_volume: Optional[int]
    breakout_price: Optional[float]
    alert_sent_10d: bool
    alert_sent_bottom: bool
    created_at: datetime
    updated_at: datetime
