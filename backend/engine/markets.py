"""Market profiles: everything that differs between markets but not between instruments.

The analysis rules are identical everywhere; what changes per market is the
calendar (24/7 crypto vs weekday sessions), whether volume is meaningful
(forex has no consolidated volume), the liquidity threshold and its currency,
costs, and annualisation. Each market is scanned, backtested and stored
separately under its own id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .config import CostModel


@dataclass(frozen=True)
class MarketProfile:
    id: str
    name: str
    group: str                 # INDIA | CRYPTO | GLOBAL | FOREX
    asset_class: str           # EQUITY | CRYPTO | FOREX
    calendar: str              # weekdays | 24x7
    volumeless: bool           # True → volume rules and traded-value liquidity do not apply
    strategy_set: str          # equity | price_only
    liquidity_currency: str    # currency of validation.min_avg_traded_value
    validation: Dict[str, float] = field(default_factory=dict)
    costs: CostModel = field(default_factory=CostModel)
    periods_per_year: int = 252
    short_note: str = ""
    exchanges: List[str] = field(default_factory=list)


PROFILES: Dict[str, MarketProfile] = {p.id: p for p in [
    MarketProfile("NSE", "India — NSE equities", "INDIA", "EQUITY", "weekdays", False, "equity", "INR",
                  {}, CostModel(0.12, 0.05), 252,
                  "Overnight shorts in Indian cash equities are not permitted; SHORT setups require F&O.", ["NSE"]),
    MarketProfile("CRYPTO", "Crypto (spot, USDT pairs)", "CRYPTO", "CRYPTO", "24x7", False, "equity", "USD",
                  {"min_avg_traded_value": 2e7, "min_price": 0.0, "max_stop_pct": 18.0, "max_staleness_days": 2},
                  CostModel(0.10, 0.05), 365,
                  "SHORT setups require perpetual futures or margin; funding costs are not included in the backtest.", ["BINANCE", "CRYPTO"]),
]}

def profile(market: str) -> MarketProfile:
    try:
        return PROFILES[market]
    except KeyError:
        raise ValueError(f"Unknown market {market!r}. Known: {sorted(PROFILES)}") from None


def pip_size(symbol: str, quote: str) -> float:
    return 0.01 if quote in ("JPY", "INR", "KRW") else 0.0001


def pips(distance: float, symbol: str, quote: str) -> float:
    return round(abs(distance) / pip_size(symbol, quote), 1)
