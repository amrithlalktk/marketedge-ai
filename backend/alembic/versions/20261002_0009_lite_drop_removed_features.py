"""lite build: drop tables of removed features (news, calendars, AI analyst, ML, custom strategies, fundamentals)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-02 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

DROP = ['news_symbols', 'news', 'earnings_events', 'economic_events', 'analyst_queries', 'ml_models', 'strategy_versions', 'strategies', 'fundamentals']


def upgrade() -> None:
    with op.batch_alter_table('backtests') as b:  # batch: also works on SQLite
        b.drop_constraint('fk_backtests_strategy_version_id', type_='foreignkey')
        b.drop_column('strategy_version_id')
    for t in DROP:
        op.drop_table(t)


def downgrade() -> None:
    # recreate the dropped (empty) tables exactly as migrations 0001/0003/0005/0006 defined them
    op.create_table('fundamentals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instrument_id', 'as_of', 'source')
    )
    op.create_index(op.f('ix_fundamentals_instrument_id'), 'fundamentals', ['instrument_id'], unique=False)
    op.create_table('strategies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('direction', sa.String(length=8), nullable=False),
    sa.Column('is_builtin', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )

    op.create_table('strategy_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('strategy_id', 'version')
    )
    op.create_index(op.f('ix_strategy_versions_strategy_id'), 'strategy_versions', ['strategy_id'], unique=False)
    op.create_table('news',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('url', sa.String(length=1000), nullable=True),
    sa.Column('source', sa.String(length=128), nullable=False),
    sa.Column('markets', sa.JSON(), nullable=False),
    sa.Column('sentiment_label', sa.String(length=16), nullable=False),
    sa.Column('sentiment_score', sa.Float(), nullable=False),
    sa.Column('sentiment_method', sa.String(length=32), nullable=False),
    sa.Column('sentiment_terms', sa.JSON(), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'external_id')
    )
    op.create_index(op.f('ix_news_category'), 'news', ['category'], unique=False)
    op.create_index(op.f('ix_news_published_at'), 'news', ['published_at'], unique=False)
    op.create_index(op.f('ix_news_sentiment_label'), 'news', ['sentiment_label'], unique=False)
    op.create_table('news_symbols',
    sa.Column('article_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('symbol', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['article_id'], ['news.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('article_id', 'symbol')
    )
    op.create_index(op.f('ix_news_symbols_symbol'), 'news_symbols', ['symbol'], unique=False)
    op.create_table('earnings_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('event_date', sa.Date(), nullable=False),
    sa.Column('period', sa.String(length=32), nullable=True),
    sa.Column('time', sa.String(length=8), nullable=True),
    sa.Column('eps_estimate', sa.Float(), nullable=True),
    sa.Column('eps_actual', sa.Float(), nullable=True),
    sa.Column('revenue_estimate', sa.Float(), nullable=True),
    sa.Column('revenue_actual', sa.Float(), nullable=True),
    sa.Column('guidance', sa.String(length=32), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instrument_id', 'event_date')
    )
    op.create_index(op.f('ix_earnings_events_event_date'), 'earnings_events', ['event_date'], unique=False)
    op.create_index(op.f('ix_earnings_events_instrument_id'), 'earnings_events', ['instrument_id'], unique=False)
    op.create_table('economic_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('event_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('country', sa.String(length=8), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=True),
    sa.Column('impact', sa.String(length=8), nullable=False),
    sa.Column('actual', sa.Float(), nullable=True),
    sa.Column('forecast', sa.Float(), nullable=True),
    sa.Column('previous', sa.Float(), nullable=True),
    sa.Column('unit', sa.String(length=16), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'external_id')
    )
    op.create_index(op.f('ix_economic_events_country'), 'economic_events', ['country'], unique=False)
    op.create_index(op.f('ix_economic_events_event_time'), 'economic_events', ['event_time'], unique=False)
    op.create_index(op.f('ix_economic_events_impact'), 'economic_events', ['impact'], unique=False)
    op.create_table('analyst_queries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('signal_id', sa.Integer(), nullable=True),
    sa.Column('symbol', sa.String(length=64), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer', sa.Text(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('stop_reason', sa.String(length=32), nullable=True),
    sa.Column('grounded', sa.Boolean(), nullable=False),
    sa.Column('unverified_numbers', sa.JSON(), nullable=False),
    sa.Column('usage', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_analyst_queries_created_at'), 'analyst_queries', ['created_at'], unique=False)
    op.create_index(op.f('ix_analyst_queries_user_id'), 'analyst_queries', ['user_id'], unique=False)
    op.create_table('ml_models',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('market', sa.String(length=16), nullable=False),
    sa.Column('target', sa.String(length=32), nullable=False),
    sa.Column('algo', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('eligible', sa.Boolean(), nullable=False),
    sa.Column('source_scan_run_id', sa.Integer(), nullable=True),
    sa.Column('metrics', sa.JSON(), nullable=False),
    sa.Column('artifact', sa.JSON(), nullable=False),
    sa.Column('features', sa.JSON(), nullable=False),
    sa.Column('engine_version', sa.String(length=16), nullable=False),
    sa.Column('is_sample_data', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_scan_run_id'], ['scan_runs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('market', 'target', 'version')
    )
    op.create_index(op.f('ix_ml_models_market'), 'ml_models', ['market'], unique=False)
    op.create_index(op.f('ix_ml_models_status'), 'ml_models', ['status'], unique=False)
    op.add_column('strategies', sa.Column('current_version', sa.Integer(), server_default='1', nullable=False))  # added in 0003
    op.add_column('strategies', sa.Column('include_in_scan', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('backtests', sa.Column('strategy_version_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_backtests_strategy_version_id', 'backtests', 'strategy_versions', ['strategy_version_id'], ['id'], ondelete='SET NULL')
