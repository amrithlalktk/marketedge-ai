"""ORM models. `instruments` unifies stocks, indices, ETFs, crypto assets and forex
pairs (distinguished by `asset_class`) so every market shares one OHLCV table.
Options, portfolio, paper-trading, alert and news tables arrive with their phases."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Table, Column,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    permissions = relationship("Permission", secondary=role_permissions, lazy="selectin")


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    role = relationship("Role", lazy="joined")


class UserSession(Base):
    """Refresh-token sessions (hash only). Rotated on every refresh; reuse revokes all."""
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    family: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ProviderCredential(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(64))
    encrypted_value: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("provider", "name"),)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class Sector(Base):
    __tablename__ = "sectors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    asset_class: Mapped[str] = mapped_column(String(16), index=True)
    exchange: Mapped[str] = mapped_column(String(16), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True, default="NSE", server_default="NSE")  # scan universe (NSE, CRYPTO, US, …)
    currency: Mapped[str] = mapped_column(String(8))
    sector_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sectors.id"), nullable=True)
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    is_index: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    listed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    delisted_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    sector = relationship("Sector", lazy="joined")


class MarketBar(Base):
    __tablename__ = "market_data"
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=False), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))


class DataStatus(Base):
    """Freshness / error status per instrument+interval (drives ⚠ DATA DELAYED)."""
    __tablename__ = "data_status"
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    last_bar_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    as_of: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    engine_version: Mapped[str] = mapped_column(String(16), default="")
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    events_backtest_id: Mapped[Optional[int]] = mapped_column(ForeignKey("backtests.id", ondelete="SET NULL"), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16))
    strategy_key: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), index=True)  # VALID | NO_TRADE
    as_of: Mapped[date] = mapped_column(Date, index=True)
    score: Mapped[float] = mapped_column(Float)
    rr_t2: Mapped[float] = mapped_column(Float)
    t1_hit_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    is_sample_data: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_signals_status_asof_score", "status", "as_of", "score"),)


class SignalOutcome(Base):
    """signal_history: forward-tracked outcome of every published VALID signal (live track record)."""
    __tablename__ = "signal_history"
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | resolved | skipped
    t1_hit: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    t2_hit: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    stop_hit: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    net_return_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Backtest(Base):
    __tablename__ = "backtests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="user")  # user | system_events
    strategy_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    backtest_id: Mapped[int] = mapped_column(ForeignKey("backtests.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(64))
    strategy_key: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(8))
    signal_date: Mapped[date] = mapped_column(Date)
    entry_date: Mapped[date] = mapped_column(Date)
    exit_date: Mapped[date] = mapped_column(Date)
    entry: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    t1: Mapped[float] = mapped_column(Float)
    t2: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    exit_reason: Mapped[str] = mapped_column(String(16))
    t1_hit: Mapped[bool] = mapped_column(Boolean)
    t2_hit: Mapped[bool] = mapped_column(Boolean)
    stop_hit: Mapped[bool] = mapped_column(Boolean)
    neither: Mapped[bool] = mapped_column(Boolean)
    bars_held: Mapped[int] = mapped_column(Integer)
    gross_return_pct: Mapped[float] = mapped_column(Float)
    net_return_pct: Mapped[float] = mapped_column(Float)
    r_multiple: Mapped[float] = mapped_column(Float)
    mfe_r: Mapped[float] = mapped_column(Float)
    mae_r: Mapped[float] = mapped_column(Float)
    rr_t2_planned: Mapped[float] = mapped_column(Float)
    regime: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    regime_family: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    score_at_signal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_bucket: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    risk_atr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ambiguous: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    t1_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    __table_args__ = (Index("ix_bt_trades_bt_strategy", "backtest_id", "strategy_key"),)


class MarketRegime(Base):
    __tablename__ = "market_regimes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16))
    as_of: Mapped[date] = mapped_column(Date)
    regime: Mapped[str] = mapped_column(String(32))
    family: Mapped[str] = mapped_column(String(16))
    volatility: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("market", "as_of"),)


class MarketSnapshot(Base):
    """Latest computed dashboards: overview | breadth | sectors | strategy_performance."""
    __tablename__ = "market_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scan_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    market: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_snapshots_market_kind", "market", "kind", "created_at"),)


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    market: Mapped[str] = mapped_column(String(16), default="NSE")  # NSE | NIFTY | CRYPTO | GLOBAL | FOREX
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items = relationship("WatchlistItem", cascade="all, delete-orphan", lazy="selectin")
    __table_args__ = (UniqueConstraint("user_id", "name"),)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(String(500), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    instrument = relationship("Instrument", lazy="joined")
    __table_args__ = (UniqueConstraint("watchlist_id", "instrument_id"),)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # ingest | scan | backtest
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ----------------------------------------------------------------------------- Phase 2: options
class OptionContract(Base):
    __tablename__ = "options_contracts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    underlying_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    expiry: Mapped[date] = mapped_column(Date, index=True)
    strike: Mapped[float] = mapped_column(Float)
    option_type: Mapped[str] = mapped_column(String(2))  # CE | PE
    lot_size: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("underlying_id", "expiry", "strike", "option_type"),)


class OptionChainRow(Base):
    """One contract's end-of-day (or intraday) quote in a chain snapshot."""
    __tablename__ = "options_chain"
    contract_id: Mapped[int] = mapped_column(ForeignKey("options_contracts.id", ondelete="CASCADE"), primary_key=True)
    snapshot_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    underlying_price: Mapped[float] = mapped_column(Float)
    bid: Mapped[float] = mapped_column(Float)
    ask: Mapped[float] = mapped_column(Float)
    ltp: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    oi: Mapped[float] = mapped_column(Float)
    oi_change: Mapped[float] = mapped_column(Float)
    iv: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # vendor IV if supplied; engine solves otherwise
    source: Mapped[str] = mapped_column(String(32))
    __table_args__ = (Index("ix_options_chain_snapshot", "snapshot_ts"),)


class OptionIVHistory(Base):
    """Daily near-expiry ATM IV per underlying — basis for IV percentile."""
    __tablename__ = "options_iv_history"
    underlying_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    atm_iv: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))


# ----------------------------------------------------------------------------- Phase 4: crypto derivatives
class CryptoDerivative(Base):
    """Daily perpetual-futures context per crypto instrument (short history; context only)."""
    __tablename__ = "crypto_derivatives"
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    funding_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    open_interest: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    long_short_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32))


# ----------------------------------------------------------------------------- Phase 5: news, calendars, AI analyst
class Portfolio(Base):
    """kind 'paper' = simulated execution through PaperBroker; 'journal' = user-entered real trades (no execution)."""
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="paper")
    base_currency: Mapped[str] = mapped_column(String(8), default="INR")
    starting_capital: Mapped[float] = mapped_column(Float, default=1_000_000.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    trades = relationship("PortfolioTrade", cascade="all, delete-orphan", lazy="selectin", order_by="PortfolioTrade.id")
    __table_args__ = (UniqueConstraint("user_id", "name"),)


class PortfolioTrade(Base):
    """paper_trades / portfolio_positions: one row per order → position → exit."""
    __tablename__ = "portfolio_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    market: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(8), default="market")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | open | closed | cancelled
    quantity: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target1: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    partial_at_t1: Mapped[float] = mapped_column(Float, default=0.0)
    commission_pct: Mapped[float] = mapped_column(Float, default=0.12)
    slippage_pct: Mapped[float] = mapped_column(Float, default=0.05)
    taxes: Mapped[float] = mapped_column(Float, default=0.0)          # journal: user-entered taxes (base ccy)
    brokerage: Mapped[float] = mapped_column(Float, default=0.0)      # journal: user-entered brokerage (base ccy)
    filled_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    realized: Mapped[float] = mapped_column(Float, default=0.0)       # instrument currency, after costs
    costs: Mapped[float] = mapped_column(Float, default=0.0)
    fx_to_base_entry: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fx_to_base_exit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)            # engine state (open_qty, t1_done, bars_held, fills, last_bar…)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    max_hold_bars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    signal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(String(1000), default="")
    close_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    order_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))  # bar-time reference for execution (naive, like market_data.ts)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[Optional[int]] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)  # active | paused | triggered | expired
    repeat: Mapped[bool] = mapped_column(Boolean, default=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_bar: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # bar already evaluated (idempotent ticks)
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(300), default="")
    signal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trade_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    alert_id: Mapped[Optional[int]] = mapped_column(ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    deliveries: Mapped[dict] = mapped_column(JSON, default=dict)  # channel -> {status: sent|failed|skipped|not_configured, error?, at}
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class NotificationSetting(Base):
    __tablename__ = "notification_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    telegram_link_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    telegram_link_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    whatsapp_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    push_subscriptions: Mapped[list] = mapped_column(JSON, default=list)
    default_channels: Mapped[list] = mapped_column(JSON, default=lambda: ["web"])
    quiet_start_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # IST hours; web still records
    quiet_end_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


# Phase 8 hot-path indexes (migration 0008): latest scan per market, job/metrics windows,
# unread notifications, a scan's signals ranked by score.
Index("ix_scan_runs_market_status_id", ScanRun.market, ScanRun.status, ScanRun.id)
Index("ix_jobs_created_at", Job.created_at)
Index("ix_notifications_user_read", Notification.user_id, Notification.read_at)
Index("ix_signals_run_status_score", Signal.scan_run_id, Signal.status, Signal.score)
Index("ix_portfolio_trades_status_symbol", PortfolioTrade.status, PortfolioTrade.symbol)
