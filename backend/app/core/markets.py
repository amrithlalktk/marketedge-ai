"""App-level market configuration: engine profile + benchmark/VIX symbols + provider choice."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from engine.markets import PROFILES, MarketProfile, profile

from .config import get_settings


@dataclass
class MarketConfig:
    id: str
    profile: MarketProfile
    benchmark: str
    vix: Optional[str]
    provider: str


def market_config(market: str) -> MarketConfig:
    s = get_settings()
    p = profile(market)
    if market == "NSE":
        return MarketConfig(market, p, s.benchmark_symbol, s.vix_symbol, s.market_data_provider)
    if market == "CRYPTO":
        return MarketConfig(market, p, s.benchmark_crypto, None, s.crypto_data_provider)
    raise ValueError(f"Unknown market {market!r}")


def enabled_markets() -> List[str]:
    return [m for m in get_settings().markets_enabled if m in PROFILES]
