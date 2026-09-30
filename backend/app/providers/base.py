"""Provider abstraction. Each data domain has an interface; concrete adapters
(licensed broker/vendor APIs, CSV files, the SAMPLE generator) implement it
and are selected via configuration, so providers can be replaced without
touching the analysis engine or the API layer."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import List, Optional, Tuple

import pandas as pd


@dataclass
class DataMeta:
    source: str
    is_sample: bool
    as_of: Optional[str]
    fetched_at: str
    delayed: bool = False
    error: Optional[str] = None
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Instrument:
    symbol: str
    name: str
    asset_class: str  # EQUITY | INDEX | ETF | CRYPTO | FOREX | OPTION | FUTURE
    exchange: str
    currency: str
    sector: Optional[str]
    listed_on: Optional[str] = None
    delisted_on: Optional[str] = None
    is_index: bool = False
    is_sample: bool = False
    lot_size: int = 1
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class MarketDataProvider(ABC):
    name: str = "abstract"
    is_sample: bool = False

    @abstractmethod
    def list_instruments(self, market: str = "NSE") -> List[Instrument]: ...

    @abstractmethod
    def get_ohlcv(self, symbol: str, interval: str = "1d", start: Optional[date] = None,
                  end: Optional[date] = None) -> Tuple[pd.DataFrame, DataMeta]:
        """Return an ascending DatetimeIndex frame with open/high/low/close/volume."""

    def health(self) -> dict:
        return {"provider": self.name, "ok": True}


class FundamentalDataProvider(ABC):
    name = "abstract"

    @abstractmethod
    def get_fundamentals(self, symbol: str) -> Optional[dict]:
        """Keys (all optional): revenue_growth_pct, profit_growth_pct, eps_growth_pct, roe_pct, roce_pct,
        debt_to_equity, operating_margin_pct, net_margin_pct, pe, pb, peg, dividend_yield_pct,
        promoter_holding_pct, institutional_holding_pct, as_of, source."""

    def next_earnings_date(self, symbol: str) -> Optional[date]:
        return None


@dataclass
class ChainSnapshot:
    underlying: str
    as_of: "datetime"          # snapshot time (IST)
    spot: float
    lot_size: int
    chain: pd.DataFrame        # expiry, strike, option_type(CE|PE), bid, ask, ltp, volume, oi, oi_change[, iv]
    meta: DataMeta


class OptionsDataProvider(ABC):
    name = "abstract"
    is_sample = False

    @abstractmethod
    def get_option_chain(self, underlying: str, as_of: Optional[date] = None) -> ChainSnapshot:
        """Latest (or as-of) end-of-day chain snapshot for all listed expiries."""

    def get_iv_history(self, underlying: str) -> Optional[pd.Series]:
        """Optional back-history of ATM IV (decimal) by date, used to seed IV percentile."""
        return None

    def health(self) -> dict:
        return {"provider": self.name, "ok": True}


class CryptoDataProvider(MarketDataProvider):  # Phase 4
    pass


class NewsProvider(ABC):
    """Returns dicts: external_id, published_at (UTC ISO), title, summary, url, source, symbols[] (platform symbols), is_sample."""
    name = "abstract"

    @abstractmethod
    def get_news(self, symbols: List[str], since: datetime, market: str) -> List[dict]: ...


class EconomicCalendarProvider(ABC):
    """Returns dicts: external_id, event_time (UTC ISO), country (ISO-2 / EU), currency, name, category,
    impact (Low|Medium|High), actual, forecast, previous, unit, source, is_sample."""
    name = "abstract"

    @abstractmethod
    def get_events(self, start: date, end: date) -> List[dict]: ...


class EarningsCalendarProvider(ABC):
    """Returns dicts: symbol, event_date, period, time (bmo|amc|None), eps_estimate, eps_actual,
    revenue_estimate, revenue_actual, guidance, source, is_sample."""
    name = "abstract"

    @abstractmethod
    def get_earnings(self, symbols: List[str], start: date, end: date) -> List[dict]: ...


class NullFundamentalProvider(FundamentalDataProvider):
    """Used when no licensed fundamentals feed is configured: returns nothing,
    and the engine redistributes the fundamental weight (and says so)."""
    name = "none"

    def get_fundamentals(self, symbol: str) -> Optional[dict]:
        return None
