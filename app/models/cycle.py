from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional


class CycleSummary(BaseModel):
    id: int
    ticker: str
    breakout_date: date
    phase: str  # distribution_in_progress | bottoming_candidate | invalidated | done
    days_remaining: Optional[int] = None
    trading_days_elapsed: Optional[int] = None
    estimated_dist_days: Optional[int] = None
    # meeting-goc v1.5 fields
    game_type: Optional[str] = None
    rewatch_window_start: Optional[date] = None
    rewatch_window_end: Optional[date] = None
    phase_reason: Optional[str] = None
    invalidation_reason: Optional[str] = None
    breakout_zone_low: Optional[float] = None
    breakout_zone_high: Optional[float] = None
    # kept for backwards compat
    predicted_bottom_date: Optional[date] = None


class CycleDetail(CycleSummary):
    peak_volume: Optional[int] = None
    breakout_price: Optional[float] = None
    alert_sent_10d: bool = False
    alert_sent_bottom: bool = False
    created_at: datetime
    updated_at: datetime
