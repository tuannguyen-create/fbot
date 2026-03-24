"""UX context: bar_time on alerts, source_alert_id on cycles

Revision ID: 005
Revises: 004
Create Date: 2026-03-24

Changes:
- volume_alerts: add bar_time TIMESTAMPTZ NOT NULL (actual market bar time, not DB insert time)
  Backfill: derive from slot + ICT date of fired_at
  Swap dedup unique index from fired_at → bar_time so business logic uses the correct timestamp
- cycle_events: add source_alert_id FK to volume_alerts (which M1 alert triggered this cycle)
  Backfill: match by bar_time on breakout_date (not fired_at)
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

    # All rows now have bar_time — enforce NOT NULL going forward
    op.execute("ALTER TABLE volume_alerts ALTER COLUMN bar_time SET NOT NULL")

    # Swap the dedup unique index from fired_at to bar_time.
    # This makes "same ticker+slot+trading-day" dedup use the market bar time,
    # not the DB insert timestamp — the semantic source of truth.
    op.execute("DROP INDEX IF EXISTS uq_alert_ticker_slot_day")
    op.execute("""
        CREATE UNIQUE INDEX uq_alert_ticker_slot_day
        ON volume_alerts (ticker, slot, (DATE(bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh')))
    """)

    # Supporting index so bar_time-based queries can use the index
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_volume_alerts_bar_time
        ON volume_alerts(bar_time DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_volume_alerts_ticker_bar_time
        ON volume_alerts(ticker, bar_time DESC)
    """)

    # --- cycle_events: link back to the M1 alert that triggered this cycle ---
    op.execute("""
        ALTER TABLE cycle_events
        ADD COLUMN IF NOT EXISTS source_alert_id INTEGER REFERENCES volume_alerts(id)
    """)

    # Backfill: match cycle to the highest-ratio alert whose bar_time falls on breakout_date.
    # Using bar_time (not fired_at) so the join is semantically consistent.
    op.execute("""
        UPDATE cycle_events c
        SET source_alert_id = (
            SELECT a.id FROM volume_alerts a
            WHERE a.ticker = c.ticker
              AND DATE(a.bar_time AT TIME ZONE 'Asia/Ho_Chi_Minh') = c.breakout_date
            ORDER BY a.ratio_5d DESC NULLS LAST
            LIMIT 1
        )
        WHERE source_alert_id IS NULL
    """)

    # source_alert_inferred: TRUE = heuristic backfill (best-guess), FALSE = canonical (M1→M3 path)
    # All rows that already existed when this migration runs get the inferred flag.
    # New cycles created after deployment always insert FALSE (the column default).
    op.execute("""
        ALTER TABLE cycle_events
        ADD COLUMN IF NOT EXISTS source_alert_inferred BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("""
        UPDATE cycle_events
        SET source_alert_inferred = TRUE
        WHERE source_alert_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE cycle_events DROP COLUMN IF EXISTS source_alert_inferred")
    op.execute("ALTER TABLE cycle_events DROP COLUMN IF EXISTS source_alert_id")

    # Restore old fired_at-based unique index
    op.execute("DROP INDEX IF EXISTS idx_volume_alerts_ticker_bar_time")
    op.execute("DROP INDEX IF EXISTS idx_volume_alerts_bar_time")
    op.execute("DROP INDEX IF EXISTS uq_alert_ticker_slot_day")
    op.execute("""
        CREATE UNIQUE INDEX uq_alert_ticker_slot_day
        ON volume_alerts (ticker, slot, (DATE(fired_at AT TIME ZONE 'Asia/Ho_Chi_Minh')))
    """)
    op.execute("ALTER TABLE volume_alerts ALTER COLUMN bar_time DROP NOT NULL")
    op.execute("ALTER TABLE volume_alerts DROP COLUMN IF EXISTS bar_time")
