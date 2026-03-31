from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # FiinQuantX
    FIINQUANT_USERNAME: str = ""
    FIINQUANT_PASSWORD: str = ""
    FIINQUANT_TICKER_LIMIT: int = 33  # general plan limit / legacy default
    FIINQUANT_STREAM_TICKER_LIMIT: int | None = None  # concurrent live stream universe
    FIINQUANT_INTRADAY_TICKER_LIMIT: int | None = None  # historical/live intraday universe
    FIINQUANT_INTRADAY_HISTORY_DAYS: int = 30  # provider intraday retention window in calendar days (plan-dependent)

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://fbot:fbot_password@localhost:5432/fbot"
    DATABASE_URL_SYNC: str = "postgresql://fbot:fbot_password@localhost:5432/fbot"
    DATABASE_SSL: bool = True  # Set False for local dev without TLS

    # Redis (optional — empty = disabled)
    REDIS_URL: str = ""

    # Resend
    RESEND_API_KEY: str = ""
    RESEND_FROM: str = "alerts@fbot.vn"
    RESEND_TO: str = "tuan.nguyen@finful.co"

    # Telegram (optional — empty = disabled)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_IDS: str = ""  # comma-separated chat IDs
    M1_TELEGRAM_FIRED_MIN_RATIO: float = 2.0
    M1_TELEGRAM_FIRED_MIN_VOLUME: int = 25_000
    M1_TELEGRAM_EXTREME_RATIO: float = 4.0
    M1_TELEGRAM_EXTREME_VOLUME: int = 150_000
    M1_TELEGRAM_CONFIRM_MIN_RATIO: float = 1.3
    M1_TELEGRAM_CONFIRM_MIN_VOLUME: int = 25_000
    M1_TELEGRAM_FIRED_REPEAT_RATIO_MULTIPLIER: float = 1.5
    M1_TELEGRAM_FIRED_REPEAT_VOLUME_MULTIPLIER: float = 2.0
    M1_TELEGRAM_CONFIRM_REPEAT_RATIO_MULTIPLIER: float = 1.3
    M1_TELEGRAM_CONFIRM_REPEAT_VOLUME_MULTIPLIER: float = 1.5

    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Ho_Chi_Minh"
    FRONTEND_URL: str = "http://localhost:3000"
    ADMIN_API_KEY: str = ""    # Set in production to restrict /admin endpoints

    # Alert thresholds
    THRESHOLD_NORMAL: float = 2.0
    THRESHOLD_MAGIC: float = 1.5
    THRESHOLD_CONFIRM_15M: float = 1.3
    BREAKOUT_VOL_MULT: float = 3.0
    BREAKOUT_PRICE_PCT: float = 0.03
    ALERT_DAYS_BEFORE_CYCLE: int = 10

    # VN30 + 3 game stocks (max 33 for FiinQuant free tier)
    WATCHLIST: List[str] = [
        # VN30 (Q1/2026 basket)
        "ACB", "BCM", "BID", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG", "LPB",
        "MBB", "MSN", "MWG", "PLX", "SAB", "SHB", "SSB", "SSI", "STB", "TCB",
        "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VPL", "VRE", "VPG",
        # +3 game stocks
        "NVL", "PDR", "KBC",
    ]

    @property
    def RESEND_RECIPIENTS(self) -> List[str]:
        return [r.strip() for r in self.RESEND_TO.split(",") if r.strip()]

    @property
    def IS_DEV(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def EFFECTIVE_STREAM_TICKER_LIMIT(self) -> int:
        return self.FIINQUANT_STREAM_TICKER_LIMIT or self.FIINQUANT_TICKER_LIMIT

    @property
    def EFFECTIVE_INTRADAY_TICKER_LIMIT(self) -> int:
        return self.FIINQUANT_INTRADAY_TICKER_LIMIT or self.EFFECTIVE_STREAM_TICKER_LIMIT


settings = Settings()
