"""Selects concrete providers from configuration.

NSE + NIFTY options: `upstox` (real) or `sample` (synthetic, DEMO_). CRYPTO: `binance` (real) or `sample`.
"""
from __future__ import annotations

from typing import Callable, Dict

from app.core.config import get_settings

from .base import FundamentalDataProvider, MarketDataProvider, NullFundamentalProvider


def provider_secret(provider: str, name: str):
    """Decrypt a provider credential stored via the admin API (never exposed to clients)."""
    try:
        from sqlalchemy import select

        from app.core.db import SessionLocal
        from app.core.security import decrypt
        from app.models import ProviderCredential

        db = SessionLocal()
        try:
            row = db.scalar(select(ProviderCredential).where(ProviderCredential.provider == provider, ProviderCredential.name == name,
                                                             ProviderCredential.enabled.is_(True)))
            return decrypt(row.encrypted_value) if row else None
        finally:
            db.close()
    except Exception:
        return None


# ---------------------------------------------------------------- Upstox (NSE + NIFTY options)
_UPSTOX_MEMO: dict = {"at": None, "value": None}  # at=None forces a re-read (after a connect / token change)


def upstox_token() -> dict:
    """Current Upstox token from the encrypted key store (memoised 60 s; a reconnect is picked up within a minute)."""
    import time as _t

    if _UPSTOX_MEMO["at"] is None or _t.monotonic() - _UPSTOX_MEMO["at"] > 60:
        analytics = provider_secret("upstox", "ANALYTICS_TOKEN")  # 1-year read-only token: preferred, no daily login
        value = ({"access_token": analytics, "expires_at": provider_secret("upstox", "ANALYTICS_TOKEN_EXPIRES"), "kind": "analytics"}
                 if analytics else {"access_token": provider_secret("upstox", "ACCESS_TOKEN"),
                                    "expires_at": provider_secret("upstox", "ACCESS_TOKEN_EXPIRES"), "kind": "daily"})
        _UPSTOX_MEMO.update(at=_t.monotonic(), value=value)
    return _UPSTOX_MEMO["value"]


def upstox_client():
    from .upstox import UpstoxClient

    return UpstoxClient(upstox_token)


# ---------------------------------------------------------------- market data
def _sample_nse() -> MarketDataProvider:
    from .sample import SampleMarketDataProvider

    return SampleMarketDataProvider(n_stocks=get_settings().sample_universe_size)


def _upstox() -> MarketDataProvider:
    from .upstox import UpstoxProvider

    s = get_settings()
    return UpstoxProvider(upstox_client(), history_days=s.upstox_history_days, universe_size=s.upstox_universe_size)


MARKET_PROVIDERS: Dict[str, Callable[[], MarketDataProvider]] = {"sample": _sample_nse, "upstox": _upstox}
FUNDAMENTAL_PROVIDERS: Dict[str, Callable[[], FundamentalDataProvider]] = {"none": NullFundamentalProvider}


def market_provider(name: str = None, market: str = "NSE") -> MarketDataProvider:
    s = get_settings()
    if market == "CRYPTO":
        name = name or s.crypto_data_provider
        if name == "sample":
            from .sample_crypto import SampleCryptoProvider

            return SampleCryptoProvider("CRYPTO")
        if name == "binance":
            from .binance import BinanceProvider

            return BinanceProvider(s.binance_spot_url, s.binance_futures_url, s.binance_universe_size, history_days=s.binance_history_days,
                                   exclude=s.binance_exclude, pit_universe=s.binance_pit_universe)
        raise ValueError(f"Unknown crypto provider {name!r} (sample | binance)")
    if market != "NSE":
        raise ValueError(f"Unknown market {market!r} (NSE | CRYPTO)")
    name = name or s.market_data_provider
    if name not in MARKET_PROVIDERS:
        raise ValueError(f"Unknown market data provider {name!r}. Available: {sorted(MARKET_PROVIDERS)}")
    return MARKET_PROVIDERS[name]()


def fundamental_provider(name: str = None) -> FundamentalDataProvider:
    return NullFundamentalProvider()


# ---------------------------------------------------------------- options
def _opt_sample():
    from .sample_options import SampleOptionsProvider

    return SampleOptionsProvider(n_stocks=get_settings().sample_universe_size)


def _opt_upstox():
    from .upstox import UpstoxOptionsProvider

    return UpstoxOptionsProvider(upstox_client())


OPTIONS_PROVIDERS = {"sample": _opt_sample, "upstox": _opt_upstox}


def options_provider(name: str = None):
    name = name or get_settings().options_data_provider
    if name not in OPTIONS_PROVIDERS:
        raise ValueError(f"Unknown options provider {name!r}. Available: {sorted(OPTIONS_PROVIDERS)}")
    return OPTIONS_PROVIDERS[name]()
