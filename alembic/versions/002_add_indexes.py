"""Add additional indexes

Revision ID: 002
Revises: 001
Create Date: 2026-03-22
"""
from alembic import op

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_ohlcv_date
        ON daily_ohlcv(date DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cycle_events_phase
        ON cycle_events(phase) WHERE phase != 'done'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_cycle_events_ticker
        ON cycle_events(ticker, breakout_date DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_notification_log_alert
        ON notification_log(alert_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_daily_ohlcv_date")
    op.execute("DROP INDEX IF EXISTS idx_cycle_events_phase")
    op.execute("DROP INDEX IF EXISTS idx_cycle_events_ticker")
    op.execute("DROP INDEX IF EXISTS idx_notification_log_alert")
