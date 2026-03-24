from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # FiinQuantX
    FIINQUANT_USERNAME: str = ""
    FIINQUANT_PASSWORD: str = ""

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

    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Asia/Ho_Chi_Minh"
    FRONTEND_URL: str = "http://localhost:3000"
    ADMIN_API_KEY: str = ""  # Empty = no auth required (dev); set in production .env

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


settings = Settings()
