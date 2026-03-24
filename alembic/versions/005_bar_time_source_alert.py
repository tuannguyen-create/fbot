"""UX context: bar_time on alerts, source_alert_id on cycles

Revision ID: 005
Revises: 004
Create Date: 2026-03-24

Changes:
- volume_alerts: add bar_time TIMESTAMPTZ (actual market bar time, not DB insert time)
  Backfill: derive from slot + ICT date of fired_at
- cycle_events: add source_alert_id FK to volume_alerts (which M1 alert triggered this cycle)
"""
from alembic import op

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- volume_alerts: actual market bar time ---
    op.execute("ALTER TABLE volume_alerts ADD COLUMN IF NOT EXISTS bar_time TIMESTAMPTZ")

    # Backfill: slot 0-149 → 09:00+slot minutes ICT, slot 150-239 → 13:00+(slot-150) minutes ICT
    op.execute("""
        UPDATE volume_alerts
        SET bar_time = CASE
            WHEN slot < 150 THEN
                (DATE(fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh')
                 + (540 + slot) * INTERVAL '1 minute')
                AT TIME ZONE 'Asia/Ho_Chi_Minh'
            ELSE
                (DATE(fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh')
                 + (780 + (slot - 150)) * INTERVAL '1 minute')
                AT TIME ZONE 'Asia/Ho_Chi_Minh'
        END
        WHERE bar_time IS NULL
    """)

    # --- cycle_events: link back to the M1 alert that triggered this cycle ---
    op.execute("""
        ALTER TABLE cycle_events
        ADD COLUMN IF NOT EXISTS source_alert_id INTEGER REFERENCES volume_alerts(id)
    """)

    # Backfill: match cycle to the highest-ratio alert on the same ticker+date
    op.execute("""
        UPDATE cycle_events c
        SET source_alert_id = (
            SELECT a.id FROM volume_alerts a
            WHERE a.ticker = c.ticker
              AND DATE(a.fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh') = c.breakout_date
            ORDER BY a.ratio_5d DESC NULLS LAST
            LIMIT 1
        )
        WHERE source_alert_id IS NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE cycle_events DROP COLUMN IF EXISTS source_alert_id")
    op.execute("ALTER TABLE volume_alerts DROP COLUMN IF EXISTS bar_time")
