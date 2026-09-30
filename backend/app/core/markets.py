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
    bench = {"CRYPTO": s.benchmark_crypto, "US": s.benchmark_us, "EUROPE": s.benchmark_europe, "ASIA": s.benchmark_asia, "FX": s.benchmark_fx}[market]
    vix = s.vix_us if market == "US" else None
    prov = s.crypto_data_provider if market == "CRYPTO" else s.fx_data_provider if market == "FX" else s.global_data_provider
    return MarketConfig(market, p, bench, vix, prov)


def enabled_markets() -> List[str]:
    return [m for m in get_settings().markets_enabled if m in PROFILES]
