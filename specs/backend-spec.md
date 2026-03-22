# fbot Backend Specification — Complete
**Version**: 1.0 | **Date**: 2026-03-22 | **Author**: PM (Claude)

---

## Overview

**fbot** là hệ thống cảnh báo chứng khoán Việt Nam tự động. Phát hiện bất thường khối lượng và chu kỳ giá theo thời gian thực, gửi email qua Resend.

**Scope MVP**: Module 1 (Volume Scanner) + Module 3 (Cycle Analysis). Module 2 (Pattern Recognition) = out of scope.

---

## 1. Architecture Overview

```
FiinQuantX WebSocket (realtime=True)
         │
         ▼
  StreamIngester (asyncio)
   └── parse 1m bar: ticker, timestamp, OHLCV, bu, sd, fb, fs, fn
         │
    ┌────┴────────────────────┐
    ▼                         ▼
Redis (baseline cache)   PostgreSQL (persistence)
    │                         │
    ▼                         ▼
Alert Engine M1          Alert Engine M3
 (Volume Scanner)        (Cycle Analysis)
    │                         │
    └────────┬────────────────┘
             ▼
     Notification Service
       (Resend Email)
             │
             ▼
    PostgreSQL (notification_log)
             │
             ▼
    FastAPI REST API ──── React/Next.js Frontend
```

**Key design decisions:**
- Một WebSocket connection cho tất cả 33 tickers (FiinQuant limit: 1 connection free tier)
- asyncio single-threaded: StreamIngester + Alert Engine chạy cùng event loop
- APScheduler (AsyncIOScheduler) chạy trong cùng event loop để baseline jobs không block stream
- Redis = in-memory cache cho baselines (fast lookup), PostgreSQL = source of truth
- Baseline rebuild mỗi đêm 18:00 ICT (sau đóng cửa) — không rebuild intraday

---

## 2. Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Language | Python | 3.11 |
| Data Feed | FiinQuantX SDK | latest |
| Database | PostgreSQL | 15 |
| Cache | Redis | 7 |
| Job Scheduler | APScheduler | 4.x (asyncio) |
| Web Framework | FastAPI | 0.110+ |
| Email | Resend Python SDK | latest |
| Containerization | Docker + Docker Compose | latest |
| ORM/Query | asyncpg (raw SQL) | latest |
| Env Config | python-dotenv | latest |
| Logging | Python logging + structlog | latest |

**Install FiinQuantX (non-standard registry):**
```bash
pip install --extra-index-url https://fiinquant.github.io/fiinquantx/simple fiinquantx
pip install matplotlib  # required dependency not auto-installed
```

---

## 3. Environment Variables (.env)

```bash
# === FiinQuantX ===
FIINQUANT_USERNAME=tuan.nguyen@finful.co
FIINQUANT_PASSWORD=12345678aB@

# === PostgreSQL ===
DATABASE_URL=postgresql+asyncpg://fbot:fbot_password@postgres:5432/fbot
DATABASE_URL_SYNC=postgresql://fbot:fbot_password@postgres:5432/fbot  # for APScheduler job store

# === Redis ===
REDIS_URL=redis://redis:6379/0

# === Resend Email ===
RESEND_API_KEY=re_xxxxxxxxxxxx
RESEND_FROM=alerts@fbot.vn
RESEND_TO=tuan.nguyen@finful.co  # comma-separated for multiple recipients

# === App Config ===
APP_ENV=development  # development | production
LOG_LEVEL=INFO  # DEBUG | INFO | WARNING | ERROR
TIMEZONE=Asia/Ho_Chi_Minh

# === Alert Thresholds (overridable) ===
THRESHOLD_NORMAL=2.0
THRESHOLD_MAGIC=1.5
THRESHOLD_CONFIRM_15M=1.3
BREAKOUT_VOL_MULT=3.0
BREAKOUT_PRICE_PCT=0.03
ALERT_DAYS_BEFORE_CYCLE=10
```

**.env.example** phải được commit vào repo. **.env** không được commit (thêm vào .gitignore).

---

## 4. Project Structure

```
fbot/
├── docker-compose.yml
├── docker-compose.prod.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── alembic/               # DB migrations
│   ├── env.py
│   └── versions/
│       ├── 001_initial_schema.py
│       └── 002_add_indexes.py
├── app/
│   ├── main.py            # FastAPI app + startup/shutdown
│   ├── config.py          # Settings (pydantic BaseSettings)
│   ├── database.py        # asyncpg pool + get_db()
│   ├── redis_client.py    # aioredis client
│   ├── models/            # Pydantic response models (không phải ORM)
│   │   ├── alert.py
│   │   ├── cycle.py
│   │   └── watchlist.py
│   ├── api/               # FastAPI routers
│   │   ├── alerts.py
│   │   ├── cycles.py
│   │   ├── watchlist.py
│   │   ├── settings.py
│   │   └── stream.py      # SSE endpoint
│   ├── services/
│   │   ├── stream_ingester.py   # FiinQuantX WebSocket handler
│   │   ├── alert_engine_m1.py  # Volume Scanner logic
│   │   ├── alert_engine_m3.py  # Cycle Analysis logic
│   │   ├── baseline_service.py # Baseline rebuild + Redis cache
│   │   ├── notification.py     # Resend email sender
│   │   └── market_calendar.py  # Trading day checks
│   ├── scheduler.py       # APScheduler jobs
│   └── utils/
│       ├── timezone.py
│       └── trading_hours.py
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_alert_engine_m1.py
    │   ├── test_alert_engine_m3.py
    │   └── test_baseline_service.py
    └── integration/
        └── test_api_endpoints.py
```

---

## 5. PostgreSQL Schema (Full DDL)

```sql
-- Migration 001: Initial schema

-- Watchlist
CREATE TABLE watchlist (
    ticker        VARCHAR(10) PRIMARY KEY,
    company_name  VARCHAR(200),
    exchange      VARCHAR(10) NOT NULL,       -- HOSE | HNX | UPCOM
    sector        VARCHAR(100),
    in_vn30       BOOLEAN DEFAULT FALSE,
    active        BOOLEAN DEFAULT TRUE,
    added_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Market calendar (trading days)
CREATE TABLE market_calendar (
    date          DATE PRIMARY KEY,
    is_trading_day BOOLEAN NOT NULL,
    reason        VARCHAR(100)                -- NULL if normal trading day
);

-- Daily OHLCV history
CREATE TABLE daily_ohlcv (
    ticker     VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
    date       DATE NOT NULL,
    open       NUMERIC(12,2),
    high       NUMERIC(12,2),
    low        NUMERIC(12,2),
    close      NUMERIC(12,2),
    volume     BIGINT,
    bu         BIGINT,    -- buy volume (integer count, NOT percentage)
    sd         BIGINT,    -- sell volume (integer count, NOT percentage)
    fb         BIGINT,    -- foreign buy
    fs         BIGINT,    -- foreign sell
    fn         BIGINT,    -- foreign net (fb - fs)
    PRIMARY KEY (ticker, date)
);

-- Intraday 1m bars (rolling window, keep last 20 trading days for M1 baseline)
CREATE TABLE intraday_1m (
    ticker     VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
    bar_time   TIMESTAMPTZ NOT NULL,          -- UTC
    open       NUMERIC(12,2),
    high       NUMERIC(12,2),
    low        NUMERIC(12,2),
    close      NUMERIC(12,2),
    volume     BIGINT,
    bu         BIGINT,
    sd         BIGINT,
    fb         BIGINT,
    fs         BIGINT,
    fn         BIGINT,
    PRIMARY KEY (ticker, bar_time)
);
CREATE INDEX idx_intraday_1m_ticker_time ON intraday_1m(ticker, bar_time DESC);

-- Volume baselines (per ticker, per 1m slot)
-- slot = minutes from market open (9:00 ICT = slot 0, 9:01 = slot 1, ..., 14:29 = slot 329)
CREATE TABLE volume_baselines (
    ticker       VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
    slot         SMALLINT NOT NULL,           -- 0..329
    avg_5d       BIGINT,
    avg_20d      BIGINT,
    std_dev      BIGINT,
    sample_count SMALLINT,
    updated_date DATE NOT NULL,
    PRIMARY KEY (ticker, slot)
);

-- Volume alerts (M1 output)
CREATE TABLE volume_alerts (
    id               BIGSERIAL PRIMARY KEY,
    ticker           VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
    fired_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    slot             SMALLINT NOT NULL,
    volume           BIGINT NOT NULL,
    baseline_5d      BIGINT,
    ratio_5d         NUMERIC(6,2),
    bu_pct           NUMERIC(5,2),            -- bu / (bu+sd) * 100
    foreign_net      BIGINT,                  -- fn value at time of alert
    in_magic_window  BOOLEAN DEFAULT FALSE,
    status           VARCHAR(20) DEFAULT 'fired',   -- fired | confirmed | cancelled
    confirmed_at     TIMESTAMPTZ,
    ratio_15m        NUMERIC(6,2),
    email_sent       BOOLEAN DEFAULT FALSE,
    cycle_event_id   BIGINT,                  -- FK to cycle_events if related
    CONSTRAINT uq_alert_ticker_slot_day UNIQUE (ticker, slot, (DATE(fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh')))
);
CREATE INDEX idx_volume_alerts_fired_at ON volume_alerts(fired_at DESC);
CREATE INDEX idx_volume_alerts_ticker ON volume_alerts(ticker, fired_at DESC);

-- Cycle events (M3 output)
CREATE TABLE cycle_events (
    id                    BIGSERIAL PRIMARY KEY,
    ticker                VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
    breakout_date         DATE NOT NULL,
    peak_volume           BIGINT,
    breakout_price        NUMERIC(12,2),
    estimated_dist_days   SMALLINT,          -- typical: 15-30 days
    distributed_so_far    SMALLINT DEFAULT 0, -- trading days elapsed since breakout
    trading_days_elapsed  SMALLINT DEFAULT 0,
    days_remaining        SMALLINT,
    predicted_bottom_date DATE,
    phase                 VARCHAR(20) DEFAULT 'distributing',  -- distributing | bottoming | done
    alert_sent_10d        BOOLEAN DEFAULT FALSE,
    alert_sent_bottom     BOOLEAN DEFAULT FALSE,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Notification log
CREATE TABLE notification_log (
    id         BIGSERIAL PRIMARY KEY,
    alert_id   BIGINT REFERENCES volume_alerts(id),
    cycle_id   BIGINT REFERENCES cycle_events(id),
    channel    VARCHAR(20) DEFAULT 'email',
    message_id VARCHAR(200),                  -- Resend message ID
    sent_at    TIMESTAMPTZ DEFAULT NOW(),
    status     VARCHAR(20) DEFAULT 'sent'     -- sent | failed | bounced
);
```

**Migration 002: Indexes**
```sql
CREATE INDEX idx_daily_ohlcv_date ON daily_ohlcv(date DESC);
CREATE INDEX idx_cycle_events_phase ON cycle_events(phase) WHERE phase != 'done';
CREATE INDEX idx_cycle_events_ticker ON cycle_events(ticker, breakout_date DESC);
```

---

## 6. Watchlist (33 stocks)

```python
# app/config.py
WATCHLIST = [
    # VN30 (30 stocks — Q1/2026 basket: VPL added, DGC removed)
    "ACB", "BCM", "BID", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG", "LPB",
    "MBB", "MSN", "MWG", "PLX", "SAB", "SHB", "SSB", "SSI", "STB", "TCB",
    "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VPL", "VRE", "VPG",
    # +3 game stocks
    "NVL", "PDR", "KBC",
]
# NOTE: Confirm 30th VN30 stock (VPG?) with domain expert before production
# FiinQuant free tier max = 33 stocks realtime
```

---

## 7. Vietnam Trading Calendar

```python
# app/utils/trading_hours.py
from datetime import time, date

MARKET_OPEN_ICT  = time(9, 0)
MARKET_CLOSE_ICT = time(14, 30)  # 14:29 = last bar
BREAK_START_ICT  = time(11, 30)  # mid-day break
BREAK_END_ICT    = time(13, 0)

TOTAL_SLOTS = 330  # 9:00-11:30 (150m) + 13:00-14:30 (90m) + extra = 330

# Magic windows (thời điểm lệch lối mòn cao)
MAGIC_WINDOWS = [
    (time(9, 0),  time(9, 30)),   # open
    (time(11, 0), time(11, 30)),  # pre-break
    (time(13, 0), time(13, 30)),  # post-break open
]

# Vietnam non-trading days 2026
NON_TRADING_DAYS_2026 = {
    date(2026, 1, 1),   # New Year
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20),  # Tết (5 days)
    date(2026, 4, 27),  # Hùng Vương
    date(2026, 4, 30), date(2026, 5, 1),  # Reunification + Labour
    date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2),  # National Day
}

def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NON_TRADING_DAYS_2026

def get_slot(bar_time_ict: time) -> int | None:
    """Convert ICT time → slot number (0-based minutes from market open).
    Returns None if outside trading hours."""
    if time(9, 0) <= bar_time_ict < time(11, 30):
        delta = (bar_time_ict.hour * 60 + bar_time_ict.minute) - (9 * 60)
        return delta
    elif time(13, 0) <= bar_time_ict < time(14, 30):
        delta = 150 + (bar_time_ict.hour * 60 + bar_time_ict.minute) - (13 * 60)
        return delta
    return None

def is_magic_window(bar_time_ict: time) -> bool:
    return any(start <= bar_time_ict < end for start, end in MAGIC_WINDOWS)
```

---

## 8. FiinQuantX Integration

### 8.1 StreamIngester

```python
# app/services/stream_ingester.py
import asyncio
import FiinQuantX as fq
from app.config import settings
from app.services.alert_engine_m1 import AlertEngineM1
from app.services.alert_engine_m3 import AlertEngineM3
from app.database import get_db_pool

class StreamIngester:
    def __init__(self):
        self.client = None
        self.m1 = AlertEngineM1()
        self.m3 = AlertEngineM3()
        self.loop = asyncio.get_event_loop()

    def connect(self):
        self.client = fq.FiinSession(
            username=settings.FIINQUANT_USERNAME,
            password=settings.FIINQUANT_PASSWORD
        ).login()

    def on_data(self, data: dict):
        """FiinQuantX callback — runs in background thread, schedule to event loop."""
        # data format: {ticker: str, bar_time: str, open, high, low, close, volume, bu, sd, fb, fs, fn}
        asyncio.run_coroutine_threadsafe(self._process(data), self.loop)

    async def _process(self, data: dict):
        try:
            bar = self._parse(data)
            if bar is None:
                return
            # Persist intraday bar
            await self._save_bar(bar)
            # Run alert engines
            await self.m1.process(bar)
            await self.m3.process(bar)
        except Exception as e:
            logger.error(f"StreamIngester._process error: {e}", exc_info=True)

    def _parse(self, raw: dict) -> dict | None:
        """Parse raw FiinQuantX dict → normalized bar dict."""
        # bu, sd are INTEGER volume counts (NOT percentages)
        # Validate: bu + sd should be <= volume (some bars may differ slightly due to settlement)
        try:
            bar = {
                'ticker': raw['ticker'],
                'bar_time': raw['datetime'],  # UTC string
                'open': float(raw['open']),
                'high': float(raw['high']),
                'low': float(raw['low']),
                'close': float(raw['close']),
                'volume': int(raw['volume']),
                'bu': int(raw.get('bu', 0)),
                'sd': int(raw.get('sd', 0)),
                'fb': int(raw.get('fb', 0)),
                'fs': int(raw.get('fs', 0)),
                'fn': int(raw.get('fn', 0)),
            }
            return bar
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse bar: {raw} — {e}")
            return None

    def start(self):
        self.connect()
        self.event = self.client.Fetch_Trading_Data(
            realtime=True,
            tickers=settings.WATCHLIST,
            fields=['open','high','low','close','volume','bu','sd','fb','fs','fn'],
            by='1m',
            callback=self.on_data,
            period=1  # last 1 bar to minimize memory
        )
        self.event.get_data()  # blocking — run in executor

    async def start_async(self):
        """Call from FastAPI startup. Runs stream in threadpool executor."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.start)

    async def stop(self):
        if self.event:
            self.event.close()
```

### 8.2 Reconnect Strategy

```python
# In StreamIngester.start_async():
MAX_RETRIES = 5
BACKOFF_BASE = 5   # seconds
BACKOFF_MAX  = 120

for attempt in range(MAX_RETRIES):
    try:
        await self.start_async_inner()
        break
    except Exception as e:
        wait = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_MAX)
        logger.error(f"Stream error (attempt {attempt+1}/{MAX_RETRIES}): {e}. Retry in {wait}s")
        await asyncio.sleep(wait)
        self.connect()  # re-login before retry
else:
    logger.critical("Stream failed after max retries. Manual intervention required.")
    # Send alert email to admin
    await notify_admin("FiinQuantX stream down after 5 retries")
```

---

## 9. Module 1: Volume Scanner (Alert Engine M1)

### 9.1 Logic

```python
# app/services/alert_engine_m1.py

THRESHOLD_NORMAL  = 2.0
THRESHOLD_MAGIC   = 1.5
THRESHOLD_CONFIRM = 1.3  # 15-min confirmation

class AlertEngineM1:
    def __init__(self):
        self.redis = None  # injected on startup
        self.db = None     # injected on startup
        self.pending_confirms: dict[str, dict] = {}  # ticker → pending alert data

    async def process(self, bar: dict):
        ticker = bar['ticker']
        bar_time_utc = bar['bar_time']
        bar_time_ict = convert_utc_to_ict(bar_time_utc)

        slot = get_slot(bar_time_ict.time())
        if slot is None:
            return  # Outside trading hours

        baseline = await self._get_baseline(ticker, slot)
        if baseline is None or baseline['avg_5d'] == 0:
            return  # No baseline yet

        ratio = bar['volume'] / baseline['avg_5d']
        in_magic = is_magic_window(bar_time_ict.time())
        threshold = THRESHOLD_MAGIC if in_magic else THRESHOLD_NORMAL

        if ratio >= threshold:
            await self._fire_alert(ticker, bar, slot, ratio, baseline, in_magic)

        # Check 15-min confirmation for pending alerts
        await self._check_confirmations(ticker, bar, slot)

    async def _fire_alert(self, ticker, bar, slot, ratio, baseline, in_magic):
        """Insert alert if not duplicate (unique constraint)."""
        bu = bar['bu']
        sd = bar['sd']
        bu_pct = (bu / (bu + sd) * 100) if (bu + sd) > 0 else None

        try:
            alert_id = await self.db.fetchval("""
                INSERT INTO volume_alerts
                    (ticker, slot, volume, baseline_5d, ratio_5d, bu_pct, foreign_net,
                     in_magic_window, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'fired')
                ON CONFLICT (ticker, slot, DATE(fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh'))
                DO NOTHING
                RETURNING id
            """, ticker, slot, bar['volume'], baseline['avg_5d'],
                ratio, bu_pct, bar['fn'], in_magic)

            if alert_id:
                # Store for 15-min confirmation
                self.pending_confirms[ticker] = {
                    'alert_id': alert_id,
                    'slot': slot,
                    'confirm_by_slot': slot + 15,
                }
                # Schedule email notification
                await send_volume_alert_email(alert_id)
        except Exception as e:
            logger.error(f"Failed to fire alert for {ticker}: {e}")

    async def _check_confirmations(self, ticker, bar, slot):
        pending = self.pending_confirms.get(ticker)
        if not pending:
            return
        if slot < pending['confirm_by_slot']:
            return

        # 15 min elapsed — compute cumulative volume
        cumulative = await self._get_cumulative_volume(ticker, pending['slot'], slot)
        baseline = await self._get_baseline(ticker, pending['slot'])
        ratio_15m = cumulative / (baseline['avg_5d'] * 15) if baseline['avg_5d'] else 0

        status = 'confirmed' if ratio_15m >= THRESHOLD_CONFIRM else 'cancelled'
        await self.db.execute("""
            UPDATE volume_alerts
            SET status = $1, confirmed_at = NOW(), ratio_15m = $2
            WHERE id = $3
        """, status, ratio_15m, pending['alert_id'])

        del self.pending_confirms[ticker]

    async def _get_baseline(self, ticker: str, slot: int) -> dict | None:
        """Redis-first, fallback to DB."""
        key = f"baseline:{ticker}:{slot}"
        cached = await self.redis.hgetall(key)
        if cached:
            return {k: int(v) for k, v in cached.items()}
        # Fallback to DB
        row = await self.db.fetchrow("""
            SELECT avg_5d, avg_20d, std_dev FROM volume_baselines
            WHERE ticker = $1 AND slot = $2
        """, ticker, slot)
        if row:
            await self.redis.hset(key, mapping=dict(row))
            await self.redis.expire(key, 86400)  # 24h TTL
        return dict(row) if row else None
```

### 9.2 Alert Deduplication

- **DB**: `UNIQUE (ticker, slot, DATE(fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh'))` — 1 alert per ticker per slot per trading day
- **Redis throttle**: After firing, set `alert_throttle:{ticker}:{slot}` with TTL = 1800s (30 min). Check before firing: if key exists, skip
- **Logic**: DB unique = idempotency. Redis throttle = prevent rapid re-trigger within same session

---

## 10. Module 3: Cycle Analysis (Alert Engine M3)

### 10.1 Detection Logic

```python
# app/services/alert_engine_m3.py

BREAKOUT_VOL_MULT   = 3.0   # 3× MA20 daily volume
BREAKOUT_PRICE_PCT  = 0.03  # +3% price vs prev close
DIST_WINDOW         = 10    # check distribution within 10 trading days post-breakout
ALERT_DAYS_BEFORE   = 10    # send email 10 days before predicted bottom
BOTTOMING_VOL_RATIO = 0.5   # < 50% MA20 volume for 3 consecutive days
BOTTOMING_DAYS_MIN  = 3

class AlertEngineM3:
    """
    Operates on DAILY data (end-of-day bars).
    Called once per day at market close via APScheduler job.
    """

    async def run_daily(self):
        """APScheduler calls this at 15:00 ICT (after 14:30 close)."""
        trading_dates = await self._get_last_n_trading_days(20)
        for ticker in settings.WATCHLIST:
            try:
                await self._analyze(ticker, trading_dates)
            except Exception as e:
                logger.error(f"M3 analysis error for {ticker}: {e}")

    async def _analyze(self, ticker: str, recent_dates: list):
        daily_rows = await self.db.fetch("""
            SELECT date, close, volume FROM daily_ohlcv
            WHERE ticker = $1 AND date >= $2
            ORDER BY date ASC
        """, ticker, recent_dates[-20])

        if len(daily_rows) < 21:
            return  # Insufficient history

        volumes = [r['volume'] for r in daily_rows]
        ma20 = sum(volumes[-20:]) / 20

        today = daily_rows[-1]
        prev  = daily_rows[-2]

        # --- Breakout Detection ---
        price_chg = (today['close'] - prev['close']) / prev['close']
        vol_ratio = today['volume'] / ma20

        if vol_ratio >= BREAKOUT_VOL_MULT and price_chg >= BREAKOUT_PRICE_PCT:
            # Check no active cycle for this ticker
            active = await self.db.fetchrow("""
                SELECT id FROM cycle_events
                WHERE ticker = $1 AND phase = 'distributing'
            """, ticker)
            if not active:
                await self._create_cycle(ticker, today, ma20)
                return

        # --- Update existing cycles ---
        cycles = await self.db.fetch("""
            SELECT * FROM cycle_events
            WHERE ticker = $1 AND phase IN ('distributing', 'bottoming')
        """, ticker)
        for cycle in cycles:
            await self._update_cycle(ticker, cycle, daily_rows, ma20)

    async def _create_cycle(self, ticker, today_row, ma20):
        est_dist_days = 20  # default assumption: 20 trading days distribution
        predicted_bottom = await self._offset_trading_days(today_row['date'], est_dist_days)
        cycle_id = await self.db.fetchval("""
            INSERT INTO cycle_events
                (ticker, breakout_date, peak_volume, breakout_price,
                 estimated_dist_days, predicted_bottom_date, phase)
            VALUES ($1, $2, $3, $4, $5, $6, 'distributing')
            RETURNING id
        """, ticker, today_row['date'], today_row['volume'], today_row['close'],
            est_dist_days, predicted_bottom)
        await send_cycle_breakout_email(cycle_id)

    async def _update_cycle(self, ticker, cycle, recent_rows, ma20):
        elapsed = await self._count_trading_days(cycle['breakout_date'], date.today())
        remaining = max(0, cycle['estimated_dist_days'] - elapsed)

        # 10-day warning
        if remaining <= ALERT_DAYS_BEFORE and not cycle['alert_sent_10d']:
            await send_cycle_10day_warning_email(cycle['id'])
            await self.db.execute("UPDATE cycle_events SET alert_sent_10d=TRUE WHERE id=$1", cycle['id'])

        # Bottom detection: 3 consecutive days < 50% MA20 volume
        last3_vols = [r['volume'] for r in recent_rows[-BOTTOMING_DAYS_MIN:]]
        all_low = all(v < ma20 * BOTTOMING_VOL_RATIO for v in last3_vols)
        if all_low and remaining <= 0:
            await self.db.execute("""
                UPDATE cycle_events
                SET phase = 'bottoming', alert_sent_bottom = TRUE,
                    trading_days_elapsed = $2, updated_at = NOW()
                WHERE id = $1
            """, cycle['id'], elapsed)
            await send_cycle_bottom_email(cycle['id'])
        else:
            await self.db.execute("""
                UPDATE cycle_events SET days_remaining = $2,
                    trading_days_elapsed = $3, distributed_so_far = $3, updated_at = NOW()
                WHERE id = $1
            """, cycle['id'], remaining, elapsed)
```

---

## 11. Baseline Service

```python
# app/services/baseline_service.py

class BaselineService:
    """Rebuild volume baselines from intraday_1m history."""

    async def rebuild_all(self):
        """APScheduler job: runs at 18:00 ICT every trading day."""
        for ticker in settings.WATCHLIST:
            await self.rebuild_ticker(ticker)
        logger.info(f"Baseline rebuild complete for {len(settings.WATCHLIST)} tickers")

    async def rebuild_ticker(self, ticker: str):
        """
        For each of 330 slots, compute avg_5d and avg_20d from intraday_1m history.
        avg_5d = PRIMARY signal (robust vs Tết gaps)
        avg_20d = SECONDARY signal
        """
        rows = await self.db.fetch("""
            SELECT bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh' AS bar_ict, volume
            FROM intraday_1m
            WHERE ticker = $1
              AND bar_time >= NOW() - INTERVAL '25 days'
            ORDER BY bar_time ASC
        """, ticker)

        # Group by slot
        slot_volumes: dict[int, list[int]] = defaultdict(list)
        for row in rows:
            slot = get_slot(row['bar_ict'].time())
            if slot is not None:
                slot_volumes[slot].append(row['volume'])

        now_date = date.today()
        for slot, vols in slot_volumes.items():
            avg_5d  = int(mean(vols[-5:])) if len(vols) >= 5 else None
            avg_20d = int(mean(vols[-20:])) if len(vols) >= 20 else None
            std_dev = int(stdev(vols[-20:])) if len(vols) >= 20 else None

            await self.db.execute("""
                INSERT INTO volume_baselines (ticker, slot, avg_5d, avg_20d, std_dev, sample_count, updated_date)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (ticker, slot)
                DO UPDATE SET avg_5d=$3, avg_20d=$4, std_dev=$5, sample_count=$6, updated_date=$7
            """, ticker, slot, avg_5d, avg_20d, std_dev, len(vols), now_date)

            # Update Redis
            key = f"baseline:{ticker}:{slot}"
            if avg_5d:
                await self.redis.hset(key, mapping={'avg_5d': avg_5d, 'avg_20d': avg_20d or 0, 'std_dev': std_dev or 0})
                await self.redis.expire(key, 86400)
```

**First-run backfill:**
```python
async def backfill_history(ticker: str, days: int = 20):
    """
    Called once on first startup if volume_baselines is empty.
    Uses Fetch_Trading_Data(realtime=False, period=days) to get historical 1m bars.
    FiinQuant free: max 30 days 1m history.
    """
    client.Fetch_Trading_Data(
        realtime=False,
        tickers=[ticker],
        fields=['volume','bu','sd','fb','fs','fn'],
        by='1m',
        callback=save_historical_bar,
        period=days
    ).get_data()
    await rebuild_ticker(ticker)
```

---

## 12. APScheduler Jobs

```python
# app/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

scheduler = AsyncIOScheduler(
    jobstores={'default': SQLAlchemyJobStore(url=settings.DATABASE_URL_SYNC)},
    timezone='Asia/Ho_Chi_Minh'
)

def setup_jobs():
    # Rebuild baselines after market close (daily, trading days only)
    scheduler.add_job(
        baseline_service.rebuild_all,
        trigger='cron',
        hour=18, minute=0,
        id='baseline_rebuild',
        replace_existing=True,
        misfire_grace_time=3600
    )

    # Run M3 daily analysis (after market close)
    scheduler.add_job(
        alert_engine_m3.run_daily,
        trigger='cron',
        hour=15, minute=5,
        id='m3_daily',
        replace_existing=True,
        misfire_grace_time=3600
    )

    # Update market calendar (first day of each month)
    scheduler.add_job(
        market_calendar.update_calendar,
        trigger='cron',
        day=1, hour=7, minute=0,
        id='calendar_update',
        replace_existing=True
    )

    # Cleanup old intraday_1m data (keep 25 days only)
    scheduler.add_job(
        cleanup_old_intraday,
        trigger='cron',
        hour=19, minute=0,
        id='cleanup_intraday',
        replace_existing=True
    )

    scheduler.start()
```

**Job guard — skip on non-trading days:**
```python
async def baseline_rebuild_guarded():
    if not is_trading_day(date.today()):
        logger.info("Skipping baseline rebuild (non-trading day)")
        return
    await baseline_service.rebuild_all()
```

---

## 13. FastAPI REST API

### 13.1 App Setup

```python
# app/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db_pool.init()
    await redis_client.init()
    stream_ingester.inject_deps(db_pool, redis_client)
    alert_engine_m1.inject_deps(db_pool, redis_client)
    alert_engine_m3.inject_deps(db_pool, redis_client)
    baseline_service.inject_deps(db_pool, redis_client)
    await check_first_run_backfill()  # backfill if needed
    setup_jobs()
    asyncio.create_task(stream_ingester.start_async())
    yield
    # Shutdown
    await stream_ingester.stop()
    scheduler.shutdown()
    await db_pool.close()
    await redis_client.close()

app = FastAPI(title="fbot API", lifespan=lifespan)
app.include_router(alerts_router, prefix="/api/v1/alerts")
app.include_router(cycles_router, prefix="/api/v1/cycles")
app.include_router(watchlist_router, prefix="/api/v1/watchlist")
app.include_router(settings_router, prefix="/api/v1/settings")
app.include_router(stream_router, prefix="/api/v1/stream")
```

### 13.2 API Endpoints

**All responses follow contract:**
```json
{ "success": true, "data": { ... } }
{ "success": false, "error": "message" }
```

#### Alerts

```
GET  /api/v1/alerts
     Query: ticker?, date_from?, date_to?, status?, limit=50, offset=0
     Response: { data: { alerts: [...], total: int } }

GET  /api/v1/alerts/{id}
     Response: { data: { alert: AlertDetail } }

GET  /api/v1/alerts/summary/today
     Response: { data: { total: int, confirmed: int, by_ticker: {...} } }
```

#### Cycles

```
GET  /api/v1/cycles
     Query: phase?, ticker?, limit=20, offset=0
     Response: { data: { cycles: [...], total: int } }

GET  /api/v1/cycles/{id}
     Response: { data: { cycle: CycleDetail } }
```

#### Watchlist

```
GET  /api/v1/watchlist
     Response: { data: { tickers: [{ ticker, company_name, in_vn30, active }] } }

GET  /api/v1/watchlist/{ticker}/summary
     Response: { data: { ticker, today_alerts: int, active_cycle: CycleDetail|null } }
```

#### Stream (SSE)

```
GET  /api/v1/stream/alerts
     Accept: text/event-stream
     Events:
       - type: "volume_alert" — payload: AlertSummary
       - type: "cycle_alert" — payload: CycleSummary
       - type: "heartbeat" — every 30s (keepalive)
```

#### Settings

```
GET  /api/v1/settings
     Response: { data: { thresholds: {...}, watchlist_count: int, stream_status: "connected"|"disconnected" } }

PUT  /api/v1/settings/thresholds
     Body: { threshold_normal?: float, threshold_magic?: float, threshold_confirm?: float }
     Response: { data: { updated: true } }
```

#### Health

```
GET  /api/v1/health
     Response: { data: { db: "ok"|"error", redis: "ok"|"error", stream: "ok"|"error", timestamp: "..." } }
```

### 13.3 Pydantic Models

```python
# app/models/alert.py
class AlertSummary(BaseModel):
    id: int
    ticker: str
    fired_at: datetime
    slot: int
    volume: int
    ratio_5d: float | None
    bu_pct: float | None
    in_magic_window: bool
    status: str  # fired | confirmed | cancelled

class AlertDetail(AlertSummary):
    baseline_5d: int | None
    foreign_net: int | None
    confirmed_at: datetime | None
    ratio_15m: float | None

# app/models/cycle.py
class CycleSummary(BaseModel):
    id: int
    ticker: str
    breakout_date: date
    phase: str
    days_remaining: int | None
    predicted_bottom_date: date | None

class CycleDetail(CycleSummary):
    peak_volume: int | None
    breakout_price: float | None
    estimated_dist_days: int | None
    trading_days_elapsed: int | None
    alert_sent_10d: bool
    alert_sent_bottom: bool
```

---

## 14. SSE Real-time Push

```python
# app/api/stream.py
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from asyncio import Queue

router = APIRouter()
alert_queue: Queue = Queue()  # global queue

async def event_generator():
    while True:
        try:
            event = await asyncio.wait_for(alert_queue.get(), timeout=30.0)
            yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
        except asyncio.TimeoutError:
            yield "event: heartbeat\ndata: {}\n\n"

@router.get("/alerts")
async def stream_alerts():
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

When M1/M3 fires alert → call `await alert_queue.put(...)` → SSE pushes to all connected clients.

---

## 15. Notification Service (Resend Email)

### 15.1 Volume Alert Email

**Subject:** `🔥 [HPG] Khối lượng bất thường — Slot 9:15, Tỷ lệ 2.3x`

**HTML Template:**
```html
<h2 style="color:#e74c3c">⚠️ Cảnh báo Khối lượng — {{TICKER}}</h2>
<table>
  <tr><td>Thời điểm</td><td><b>{{TIME_ICT}}</b></td></tr>
  <tr><td>Khối lượng</td><td><b>{{VOLUME}}</b></td></tr>
  <tr><td>Baseline (5 ngày)</td><td>{{BASELINE_5D}}</td></tr>
  <tr><td>Tỷ lệ</td><td style="color:#e74c3c"><b>{{RATIO}}x</b></td></tr>
  <tr><td>BU%</td><td>{{BU_PCT}}%</td></tr>
  <tr><td>Foreign Net</td><td>{{FOREIGN_NET}}</td></tr>
  <tr><td>Magic Window</td><td>{{MAGIC_WINDOW_LABEL}}</td></tr>
</table>
<p style="color:#888">Xác nhận lúc {{CONFIRM_TIME}} (15 phút sau)</p>
```

### 15.2 Cycle Breakout Email

**Subject:** `📈 [NVL] Breakout phát hiện — Dự kiến phân phối {{DIST_DAYS}} ngày`

### 15.3 Cycle 10-Day Warning Email

**Subject:** `⏰ [NVL] Còn 10 ngày đến vùng tích lũy dự kiến`

### 15.4 Cycle Bottom Email

**Subject:** `🟢 [NVL] Vào vùng đáy — Khối lượng thấp 3 ngày liên tiếp`

### 15.5 Resend Implementation

```python
# app/services/notification.py
import resend

resend.api_key = settings.RESEND_API_KEY
RECIPIENTS = [r.strip() for r in settings.RESEND_TO.split(',')]

async def send_volume_alert_email(alert_id: int):
    alert = await db.fetchrow("SELECT * FROM volume_alerts WHERE id=$1", alert_id)
    html = render_volume_alert_template(alert)
    try:
        result = resend.Emails.send({
            "from": settings.RESEND_FROM,
            "to": RECIPIENTS,
            "subject": f"🔥 [{alert['ticker']}] KL bất thường — {format_slot_time(alert['slot'])}",
            "html": html,
        })
        await db.execute("""
            INSERT INTO notification_log (alert_id, channel, message_id, status)
            VALUES ($1, 'email', $2, 'sent')
        """, alert_id, result['id'])
        await db.execute("UPDATE volume_alerts SET email_sent=TRUE WHERE id=$1", alert_id)
    except Exception as e:
        logger.error(f"Resend failed for alert {alert_id}: {e}")
        await db.execute("""
            INSERT INTO notification_log (alert_id, channel, status)
            VALUES ($1, 'email', 'failed')
        """, alert_id)
```

---

## 16. Logging

```python
# app/utils/logger.py
import logging
import structlog

def setup_logging(level: str = "INFO"):
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=getattr(logging, level.upper()))
```

**Log events to capture:**
- Stream connect/disconnect/reconnect
- Every alert fired (ticker, slot, ratio)
- Email send success/failure
- Baseline rebuild start/complete/error
- APScheduler job start/complete/skip (non-trading day)
- API request errors (FastAPI exception handlers)

---

## 17. Docker Compose

```yaml
# docker-compose.yml (development)
version: '3.9'
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: fbot
      POSTGRES_PASSWORD: fbot_password
      POSTGRES_DB: fbot
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"

  fbot:
    build: .
    env_file: .env
    depends_on:
      - postgres
      - redis
    ports:
      - "8000:8000"
    volumes:
      - .:/app
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - fbot
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:8000

volumes:
  postgres_data:
  redis_data:
```

```yaml
# docker-compose.prod.yml
version: '3.9'
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: fbot
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data
    restart: unless-stopped

  fbot:
    image: fbot-backend:latest
    env_file: .env.prod
    depends_on:
      - postgres
      - redis
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - certs:/etc/ssl/certs
    depends_on:
      - fbot
      - frontend
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  certs:
```

---

## 18. Startup/Shutdown Sequence

```
STARTUP:
1. Load .env → validate required vars (raise if missing)
2. asyncpg pool.init() → test DB connection
3. aioredis.init() → test Redis connection
4. Run Alembic migrations (auto on startup in dev, manual in prod)
5. Seed watchlist table if empty
6. Seed market_calendar for current year if empty
7. Check volume_baselines: if empty → run backfill for all tickers (one-time)
8. setup_jobs() → APScheduler starts
9. asyncio.create_task(stream_ingester.start_async()) → connect FiinQuantX
10. FastAPI ready

SHUTDOWN (Ctrl+C or SIGTERM):
1. stream_ingester.stop() → close FiinQuantX WebSocket
2. scheduler.shutdown(wait=False)
3. Flush pending async writes
4. db_pool.close()
5. redis_client.close()
```

---

## 19. S1 — Error States & Validation

| Scenario | Handling |
|----------|----------|
| FiinQuantX login fails | Retry 3x with 10s delay. If all fail: log CRITICAL, skip stream |
| FiinQuantX stream drops | Exponential backoff reconnect (max 5 retries, 2-min cap) |
| PostgreSQL unreachable | FastAPI startup fails with clear error. Health endpoint returns `db: error` |
| Redis unreachable | Log WARNING. Baseline lookups fallback to DB (slower but functional) |
| Resend API fails | Log error, write `failed` to notification_log. No retry (Resend has internal retry) |
| bar data malformed | Log WARNING, skip bar, continue processing |
| Slot outside trading hours | Silently skip (normal for pre/post market data) |
| Non-trading day stream | FiinQuantX returns no data — normal. No alerts fired |
| bu + sd = 0 | bu_pct = NULL (handled in template as "N/A") |
| Duplicate alert (unique constraint violation) | Caught, silently ignored (expected by design) |

---

## 20. S2 — Post-Completion Flow

| Trigger | Immediate | Next |
|---------|-----------|------|
| Volume alert fired | Insert DB, push SSE, send email | 15-min confirmation scheduled |
| 15-min confirm | Update status (confirmed/cancelled), no 2nd email | Display update in frontend |
| Cycle breakout detected | Insert cycle_events, send email | Daily M3 job tracks progress |
| Baseline rebuild | Redis updated, DB updated | New baselines active for next trading day |

---

## 21. S3 — Cross-Feature Integration

- M1 → M3: `volume_alerts.cycle_event_id` links alert to cycle if breakout on same day
- Stream → SSE: every fired alert pushed to `alert_queue` → all SSE clients receive
- APScheduler → M3: M3 runs ONCE per day via scheduler (not per-bar)
- Baseline → M1: M1 reads Redis-cached baselines. Baseline rebuild invalidates cache
- `market_calendar` → all jobs: every scheduled job checks `is_trading_day()` before running

---

## 22. S5 — State & Persistence Matrix

| State | Storage | TTL/Cleanup |
|-------|---------|-------------|
| Volume baselines | Redis (primary) + PostgreSQL (truth) | Redis: 24h TTL. DB: updated nightly |
| Alert dedup | PostgreSQL UNIQUE constraint | Permanent |
| Alert throttle | Redis key `alert_throttle:{t}:{s}` | 30 min TTL |
| Pending 15-min confirmations | In-memory dict (AlertEngineM1) | Cleared on confirm/cancel or app restart |
| Intraday 1m bars | PostgreSQL | Keep 25 days rolling (cleanup job) |
| Daily OHLCV | PostgreSQL | Keep 1 year (FiinQuant free limit) |
| Cycle events | PostgreSQL | Permanent (for history/analysis) |
| APScheduler jobs | PostgreSQL (job store) | Managed by APScheduler |

---

## 23. S6 — Manual QA Scenarios

1. **No data weekend**: Start app on Saturday → stream connects, 0 callbacks → no alerts fired → health endpoint shows `stream: connected`
2. **First run**: Empty DB → backfill runs automatically → baselines populated → stream ready
3. **Magic window alert**: Mock bar at 9:15 with volume 2x baseline → `in_magic_window=TRUE`, threshold=1.5, fires at 1.5x
4. **Normal window alert**: Mock bar at 10:00 with 1.8x → no alert (needs 2.0x)
5. **Alert dedup**: Same ticker+slot fires twice same day → second insert ignored (unique constraint)
6. **Email send**: Fire real alert → check inbox within 60s → verify subject, ticker, ratio correct
7. **15-min confirm**: Alert fires at slot 60 → at slot 75, compute cumulative → update status
8. **Breakout cycle**: Upload mock daily data with 3x volume + 3% price → cycle_events created → email sent
9. **10-day warning**: Update cycle days_remaining to 10 manually → run M3 job → email sent → alert_sent_10d=TRUE
10. **App restart**: Restart container → in-memory pending_confirms reset → APScheduler resumes from DB job store → Redis cache repopulated on next read

---

## 24. Testing Strategy

```
tests/
├── unit/
│   ├── test_trading_hours.py  — get_slot(), is_magic_window(), is_trading_day()
│   ├── test_alert_engine_m1.py — threshold logic, bu_pct, dedup
│   ├── test_alert_engine_m3.py — breakout detection, cycle update, bottom detection
│   └── test_baseline_service.py — avg_5d/avg_20d calc, Redis upsert
└── integration/
    ├── test_api_alerts.py — GET /alerts, GET /alerts/summary/today
    ├── test_api_cycles.py — GET /cycles
    └── test_sse.py — SSE heartbeat, event push
```

**Unit test patterns:**
- Mock `asyncpg` pool with `AsyncMock`
- Mock Redis client with `AsyncMock`
- Use `pytest-asyncio` for async tests
- Feed known bar data → assert alert fired/not fired
- Assert DB insert called with correct params

**Integration tests:**
- Use `TestClient` from FastAPI
- Real PostgreSQL (test DB via Docker in CI)
- Real Redis (test instance)
