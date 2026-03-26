"""M1 quality scoring: add features JSONB + scalar quality columns to volume_alerts

Revision ID: 006
Revises: 005
Create Date: 2026-03-26
"""
from alembic import op

revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE volume_alerts
            ADD COLUMN IF NOT EXISTS features           JSONB,
            ADD COLUMN IF NOT EXISTS quality_score      SMALLINT,
            ADD COLUMN IF NOT EXISTS quality_grade      VARCHAR(1),
            ADD COLUMN IF NOT EXISTS quality_reason     TEXT,
            ADD COLUMN IF NOT EXISTS strong_bull_candle BOOLEAN,
            ADD COLUMN IF NOT EXISTS is_sideways_base   BOOLEAN
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_volume_alerts_quality
        ON volume_alerts(quality_grade, bar_time DESC)
        WHERE quality_grade IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_volume_alerts_quality")
    op.execute("""
        ALTER TABLE volume_alerts
            DROP COLUMN IF EXISTS features,
            DROP COLUMN IF EXISTS quality_score,
            DROP COLUMN IF EXISTS quality_grade,
            DROP COLUMN IF EXISTS quality_reason,
            DROP COLUMN IF EXISTS strong_bull_candle,
            DROP COLUMN IF EXISTS is_sideways_base
    """)
