"""backtest_trades: stop distance in ATRs and the same-bar ambiguity flag (losing-trade diagnostics)

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("backtest_trades") as b:
        b.add_column(sa.Column("risk_atr", sa.Float(), nullable=True))
        b.add_column(sa.Column("ambiguous", sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table("backtest_trades") as b:
        b.drop_column("ambiguous")
        b.drop_column("risk_atr")
