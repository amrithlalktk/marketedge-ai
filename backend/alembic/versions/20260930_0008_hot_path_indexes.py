"""hot-path indexes (Phase 8)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-30 18:00:00
"""
from alembic import op


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_scan_runs_market_status_id", "scan_runs", ["market", "status", "id"]),
    ("ix_jobs_created_at", "jobs", ["created_at"]),
    ("ix_notifications_user_read", "notifications", ["user_id", "read_at"]),
    ("ix_signals_run_status_score", "signals", ["scan_run_id", "status", "score"]),
    ("ix_portfolio_trades_status_symbol", "portfolio_trades", ["status", "symbol"]),
]


def upgrade() -> None:
    # On a large live database create these with CREATE INDEX CONCURRENTLY during a quiet window
    # (see docs/OPERATIONS.md); plain CREATE INDEX is fine for normal-sized installs.
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
