"""M3 Phase 1.5: eligibility filter, game taxonomy, rename phases

Revision ID: 004
Revises: 003
Create Date: 2026-03-24

Changes:
- watchlist: add eligible_for_m3, game_type (ticker-level classification)
- cycle_events: rename phases to match meeting-goc state machine
    distributing → distribution_in_progress
    bottoming    → bottoming_candidate
- Seed game_type for all 33 watchlist tickers
"""
from alembic import op

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None

# Game type classification for initial 33 tickers
_GAME_TYPES = {
    # Speculative real-estate game stocks (explicitly added outside VN30)
    "NVL": "speculative",
    "PDR": "speculative",
    "KBC": "speculative",
    # SOE / state-enterprise dominated
    "BID": "state_enterprise",
    "CTG": "state_enterprise",
    "GAS": "state_enterprise",
    "GVR": "state_enterprise",
    "PLX": "state_enterprise",
    "SAB": "state_enterprise",
    "BCM": "state_enterprise",
    "VCB": "state_enterprise",
}
_DEFAULT_GAME_TYPE = "institutional"


def upgrade() -> None:
    # --- watchlist: add M3 control fields ---
    op.execute("""
        ALTER TABLE watchlist
            ADD COLUMN IF NOT EXISTS eligible_for_m3 BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS game_type       VARCHAR(20)
    """)

    # Seed game_type
    for ticker, gtype in _GAME_TYPES.items():
        op.execute(
            f"UPDATE watchlist SET game_type = '{gtype}' WHERE ticker = '{ticker}'"
        )
    op.execute(
        f"UPDATE watchlist SET game_type = '{_DEFAULT_GAME_TYPE}' WHERE game_type IS NULL"
    )

    # --- cycle_events: rename phases ---
    op.execute("""
        UPDATE cycle_events
        SET phase = 'distribution_in_progress'
        WHERE phase = 'distributing'
    """)
    op.execute("""
        UPDATE cycle_events
        SET phase = 'bottoming_candidate'
        WHERE phase = 'bottoming'
    """)
    op.execute("""
        ALTER TABLE cycle_events
        ALTER COLUMN phase SET DEFAULT 'distribution_in_progress'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE cycle_events
        SET phase = 'distributing'
        WHERE phase = 'distribution_in_progress'
    """)
    op.execute("""
        UPDATE cycle_events
        SET phase = 'bottoming'
        WHERE phase = 'bottoming_candidate'
    """)
    op.execute("""
        ALTER TABLE cycle_events
        ALTER COLUMN phase SET DEFAULT 'distributing'
    """)
    op.execute("""
        ALTER TABLE watchlist
            DROP COLUMN IF EXISTS eligible_for_m3,
            DROP COLUMN IF EXISTS game_type
    """)
