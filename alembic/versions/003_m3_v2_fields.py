"""M3 v2: add cycle analysis fields for meeting-goc alignment

Revision ID: 003
Revises: 002
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New semantic fields replacing / augmenting predicted_bottom_date
    op.execute("""
        ALTER TABLE cycle_events
            ADD COLUMN IF NOT EXISTS game_type           VARCHAR(20),
            ADD COLUMN IF NOT EXISTS rewatch_window_start DATE,
            ADD COLUMN IF NOT EXISTS rewatch_window_end   DATE,
            ADD COLUMN IF NOT EXISTS phase_reason         TEXT,
            ADD COLUMN IF NOT EXISTS invalidation_reason  TEXT,
            ADD COLUMN IF NOT EXISTS breakout_zone_low    NUMERIC(12,2),
            ADD COLUMN IF NOT EXISTS breakout_zone_high   NUMERIC(12,2)
    """)

    # Back-fill rewatch_window from predicted_bottom_date for existing rows
    op.execute("""
        UPDATE cycle_events
        SET
            game_type = 'distribution',
            rewatch_window_start = predicted_bottom_date,
            rewatch_window_end   = predicted_bottom_date + INTERVAL '14 days',
            breakout_zone_low    = ROUND(breakout_price * 0.97, 2),
            breakout_zone_high   = ROUND(breakout_price * 1.05, 2)
        WHERE predicted_bottom_date IS NOT NULL
          AND breakout_price IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE cycle_events
            DROP COLUMN IF EXISTS game_type,
            DROP COLUMN IF EXISTS rewatch_window_start,
            DROP COLUMN IF EXISTS rewatch_window_end,
            DROP COLUMN IF EXISTS phase_reason,
            DROP COLUMN IF EXISTS invalidation_reason,
            DROP COLUMN IF EXISTS breakout_zone_low,
            DROP COLUMN IF EXISTS breakout_zone_high
    """)
