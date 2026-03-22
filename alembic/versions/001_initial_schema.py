"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker        VARCHAR(10) PRIMARY KEY,
            company_name  VARCHAR(200),
            exchange      VARCHAR(10) NOT NULL DEFAULT 'HOSE',
            sector        VARCHAR(100),
            in_vn30       BOOLEAN DEFAULT FALSE,
            active        BOOLEAN DEFAULT TRUE,
            added_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS market_calendar (
            date           DATE PRIMARY KEY,
            is_trading_day BOOLEAN NOT NULL,
            reason         VARCHAR(100)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS daily_ohlcv (
            ticker     VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
            date       DATE NOT NULL,
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
            PRIMARY KEY (ticker, date)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS intraday_1m (
            ticker     VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
            bar_time   TIMESTAMPTZ NOT NULL,
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
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_intraday_1m_ticker_time
        ON intraday_1m(ticker, bar_time DESC)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS volume_baselines (
            ticker       VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
            slot         SMALLINT NOT NULL,
            avg_5d       BIGINT,
            avg_20d      BIGINT,
            std_dev      BIGINT,
            sample_count SMALLINT,
            updated_date DATE NOT NULL,
            PRIMARY KEY (ticker, slot)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS volume_alerts (
            id               BIGSERIAL PRIMARY KEY,
            ticker           VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
            fired_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            slot             SMALLINT NOT NULL,
            volume           BIGINT NOT NULL,
            baseline_5d      BIGINT,
            ratio_5d         NUMERIC(6,2),
            bu_pct           NUMERIC(5,2),
            foreign_net      BIGINT,
            in_magic_window  BOOLEAN DEFAULT FALSE,
            status           VARCHAR(20) DEFAULT 'fired',
            confirmed_at     TIMESTAMPTZ,
            ratio_15m        NUMERIC(6,2),
            email_sent       BOOLEAN DEFAULT FALSE,
            cycle_event_id   BIGINT
        )
    """)

    # Expression-based unique index (cannot be inline UNIQUE constraint)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_ticker_slot_day
        ON volume_alerts (ticker, slot, (DATE(fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh')))
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_volume_alerts_fired_at
        ON volume_alerts(fired_at DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_volume_alerts_ticker
        ON volume_alerts(ticker, fired_at DESC)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS cycle_events (
            id                    BIGSERIAL PRIMARY KEY,
            ticker                VARCHAR(10) NOT NULL REFERENCES watchlist(ticker),
            breakout_date         DATE NOT NULL,
            peak_volume           BIGINT,
            breakout_price        NUMERIC(12,2),
            estimated_dist_days   SMALLINT DEFAULT 20,
            distributed_so_far    SMALLINT DEFAULT 0,
            trading_days_elapsed  SMALLINT DEFAULT 0,
            days_remaining        SMALLINT,
            predicted_bottom_date DATE,
            phase                 VARCHAR(20) DEFAULT 'distributing',
            alert_sent_10d        BOOLEAN DEFAULT FALSE,
            alert_sent_bottom     BOOLEAN DEFAULT FALSE,
            created_at            TIMESTAMPTZ DEFAULT NOW(),
            updated_at            TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id         BIGSERIAL PRIMARY KEY,
            alert_id   BIGINT REFERENCES volume_alerts(id),
            cycle_id   BIGINT REFERENCES cycle_events(id),
            channel    VARCHAR(20) DEFAULT 'email',
            message_id VARCHAR(200),
            sent_at    TIMESTAMPTZ DEFAULT NOW(),
            status     VARCHAR(20) DEFAULT 'sent'
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key    VARCHAR(100) PRIMARY KEY,
            value  TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Seed default settings
    op.execute("""
        INSERT INTO app_settings (key, value) VALUES
            ('threshold_normal', '2.0'),
            ('threshold_magic', '1.5'),
            ('threshold_confirm_15m', '1.3'),
            ('breakout_vol_mult', '3.0'),
            ('breakout_price_pct', '0.03'),
            ('alert_days_before_cycle', '10')
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notification_log CASCADE")
    op.execute("DROP TABLE IF EXISTS volume_alerts CASCADE")
    op.execute("DROP TABLE IF EXISTS cycle_events CASCADE")
    op.execute("DROP TABLE IF EXISTS volume_baselines CASCADE")
    op.execute("DROP TABLE IF EXISTS intraday_1m CASCADE")
    op.execute("DROP TABLE IF EXISTS daily_ohlcv CASCADE")
    op.execute("DROP TABLE IF EXISTS market_calendar CASCADE")
    op.execute("DROP TABLE IF EXISTS watchlist CASCADE")
    op.execute("DROP TABLE IF EXISTS app_settings CASCADE")
