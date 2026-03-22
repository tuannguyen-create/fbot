from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from app.models.cycle import CycleSummary


class WatchlistItem(BaseModel):
    ticker: str
    company_name: Optional[str]
    exchange: str
    sector: Optional[str]
    in_vn30: bool
    active: bool


class WatchlistSummary(BaseModel):
    ticker: str
    company_name: Optional[str]
    today_alerts: int
    active_cycle: Optional[CycleSummary]


class AppSettings(BaseModel):
    threshold_normal: float
    threshold_magic: float
    threshold_confirm_15m: float
    breakout_vol_mult: float
    breakout_price_pct: float
    alert_days_before_cycle: int
    watchlist_count: int
    stream_status: str


class ThresholdUpdate(BaseModel):
    threshold_normal: Optional[float] = None
    threshold_magic: Optional[float] = None
    threshold_confirm_15m: Optional[float] = None


class HealthStatus(BaseModel):
    db: str
    redis: str
    stream: str
    timestamp: str
