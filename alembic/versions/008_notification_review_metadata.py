"""008: notification review metadata.

Adds lightweight metadata to notification_log so UI can review what was sent
or would have been sent:
- event_type: semantic kind (m1_alert_fired, m3_daily_digest, ...)
- preview_text: plain-text preview shown in UI
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE notification_log
            ADD COLUMN IF NOT EXISTS event_type   VARCHAR(50),
            ADD COLUMN IF NOT EXISTS preview_text TEXT
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_log_channel_sent_at "
        "ON notification_log(channel, sent_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_log_event_type "
        "ON notification_log(event_type, sent_at DESC)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_notification_log_event_type")
    op.execute("DROP INDEX IF EXISTS idx_notification_log_channel_sent_at")
    op.execute(
        """
        ALTER TABLE notification_log
            DROP COLUMN IF EXISTS event_type,
            DROP COLUMN IF EXISTS preview_text
        """
    )
