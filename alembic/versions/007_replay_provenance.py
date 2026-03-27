"""007: replay provenance — origin tracking + replay_runs audit table.

Adds 4 columns to volume_alerts and cycle_events to distinguish live
vs historical/recovery replays. Adds replay_runs audit table.

origin values: 'live' | 'historical_replay' | 'recovery_replay'
"""
from alembic import op

revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade():
    # volume_alerts: track alert origin and replay metadata
    op.execute("""
        ALTER TABLE volume_alerts
            ADD COLUMN IF NOT EXISTS origin        VARCHAR(20)  NOT NULL DEFAULT 'live',
            ADD COLUMN IF NOT EXISTS replay_run_id UUID         NULL,
            ADD COLUMN IF NOT EXISTS replayed_at   TIMESTAMPTZ  NULL,
            ADD COLUMN IF NOT EXISTS is_actionable BOOLEAN      NOT NULL DEFAULT TRUE
    """)

    # cycle_events: same provenance tracking
    op.execute("""
        ALTER TABLE cycle_events
            ADD COLUMN IF NOT EXISTS origin        VARCHAR(20)  NOT NULL DEFAULT 'live',
            ADD COLUMN IF NOT EXISTS replay_run_id UUID         NULL,
            ADD COLUMN IF NOT EXISTS replayed_at   TIMESTAMPTZ  NULL,
            ADD COLUMN IF NOT EXISTS is_actionable BOOLEAN      NOT NULL DEFAULT TRUE
    """)

    # replay_runs: audit trail for every M1/M3 replay or backfill run
    op.execute("""
        CREATE TABLE IF NOT EXISTS replay_runs (
            id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            module          TEXT         NOT NULL,
            mode            TEXT         NOT NULL DEFAULT 'manual',
            date_from       DATE,
            date_to         DATE,
            apply           BOOLEAN      NOT NULL DEFAULT FALSE,
            notify_mode     TEXT         NOT NULL DEFAULT 'none',
            created_count   INT          NOT NULL DEFAULT 0,
            skipped_count   INT          NOT NULL DEFAULT 0,
            status          TEXT         NOT NULL DEFAULT 'running',
            started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            finished_at     TIMESTAMPTZ,
            error           TEXT
        )
    """)

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_volume_alerts_origin "
        "ON volume_alerts (origin, bar_time DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cycle_events_origin "
        "ON cycle_events (origin)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_volume_alerts_origin")
    op.execute("DROP INDEX IF EXISTS idx_cycle_events_origin")
    op.execute("DROP TABLE IF EXISTS replay_runs")
    op.execute("""
        ALTER TABLE volume_alerts
            DROP COLUMN IF EXISTS origin,
            DROP COLUMN IF EXISTS replay_run_id,
            DROP COLUMN IF EXISTS replayed_at,
            DROP COLUMN IF EXISTS is_actionable
    """)
    op.execute("""
        ALTER TABLE cycle_events
            DROP COLUMN IF EXISTS origin,
            DROP COLUMN IF EXISTS replay_run_id,
            DROP COLUMN IF EXISTS replayed_at,
            DROP COLUMN IF EXISTS is_actionable
    """)
