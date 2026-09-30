"""Selects concrete providers from configuration. Add licensed adapters here
(e.g. an authorised NSE vendor or broker API) by implementing the interfaces
in `base.py` and registering a name."""
from __future__ import annotations

from typing import Callable, Dict

from app.core.config import get_settings

from .base import FundamentalDataProvider, MarketDataProvider, NullFundamentalProvider


def _sample() -> MarketDataProvider:
    from .sample import SampleMarketDataProvider

    return SampleMarketDataProvider(n_stocks=get_settings().sample_universe_size)


def _csv() -> MarketDataProvider:
    from .csv_provider import CsvMarketDataProvider

    return CsvMarketDataProvider(get_settings().csv_data_dir)


def angel_session():
    """Angel One SmartAPI session from the encrypted provider key store (one login shared via the cache)."""
    from app.core.cache import get_cache

    from .angelone import AngelOneSession

    return AngelOneSession(provider_secret("angelone", "API_KEY"), provider_secret("angelone", "CLIENT_CODE"),
                           provider_secret("angelone", "MPIN"), provider_secret("angelone", "TOTP_SECRET"), token_cache=get_cache())


def _angelone() -> MarketDataProvider:
    from .angelone import AngelOneProvider

    return AngelOneProvider(angel_session(), history_days=get_settings().angel_history_days)


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


def _upstox() -> MarketDataProvider:
    from .upstox import UpstoxProvider

    return UpstoxProvider(upstox_client(), history_days=get_settings().upstox_history_days)


MARKET_PROVIDERS: Dict[str, Callable[[], MarketDataProvider]] = {"sample": _sample, "csv": _csv, "angelone": _angelone, "upstox": _upstox}


def _fund_csv() -> FundamentalDataProvider:
    from .csv_provider import CsvFundamentalProvider

    return CsvFundamentalProvider(get_settings().csv_data_dir)


FUNDAMENTAL_PROVIDERS: Dict[str, Callable[[], FundamentalDataProvider]] = {"none": NullFundamentalProvider, "csv": _fund_csv}


def market_provider(name: str = None, market: str = "NSE") -> MarketDataProvider:
    """Provider for a market. NSE keeps the Phase-1 providers; other markets route to
    sample | binance | twelvedata | csv according to settings."""
    s = get_settings()
    if market != "NSE":
        from app.core.markets import market_config

        name = name or market_config(market).provider
        if name == "sample":
            from .sample_global import SampleGlobalProvider

            return SampleGlobalProvider(market)
        if name == "binance":
            from .binance import BinanceProvider

            return BinanceProvider(s.binance_spot_url, s.binance_futures_url, s.binance_universe_size, history_days=s.binance_history_days,
                                   exclude=s.binance_exclude,
                                   pit_universe=s.binance_pit_universe)
        if name == "twelvedata":
            from .twelvedata import TwelveDataProvider

            return TwelveDataProvider(market, provider_secret("twelvedata", "API_KEY") or s.twelvedata_api_key, s.csv_data_dir, s.twelvedata_rate_per_min)
        if name == "csv":
            return MARKET_PROVIDERS["csv"]()
        raise ValueError(f"Unknown provider {name!r} for market {market}")
    name = name or s.market_data_provider
    if name not in MARKET_PROVIDERS:
        raise ValueError(f"Unknown market data provider {name!r}. Available: {sorted(MARKET_PROVIDERS)}")
    return MARKET_PROVIDERS[name]()


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


def fundamental_provider(name: str = None) -> FundamentalDataProvider:
    name = name or get_settings().fundamentals_provider
    return FUNDAMENTAL_PROVIDERS.get(name, NullFundamentalProvider)()


def _opt_sample():
    from .sample_options import SampleOptionsProvider

    return SampleOptionsProvider(n_stocks=get_settings().sample_universe_size)


def _opt_csv():
    from .csv_provider import CsvMarketDataProvider, CsvOptionsProvider

    root = get_settings().csv_data_dir
    return CsvOptionsProvider(root, CsvMarketDataProvider(root))


def _opt_angelone():
    from .angelone import AngelOneOptionsProvider

    return AngelOneOptionsProvider(angel_session())


def _opt_upstox():
    from .upstox import UpstoxOptionsProvider

    return UpstoxOptionsProvider(upstox_client())


OPTIONS_PROVIDERS = {"sample": _opt_sample, "csv": _opt_csv, "angelone": _opt_angelone, "upstox": _opt_upstox}


def options_provider(name: str = None):
    name = name or get_settings().options_data_provider
    if name not in OPTIONS_PROVIDERS:
        raise ValueError(f"Unknown options provider {name!r}. Available: {sorted(OPTIONS_PROVIDERS)}")
    return OPTIONS_PROVIDERS[name]()


def news_provider(name: str = None):
    s = get_settings()
    name = name or s.news_provider
    if name == "sample":
        from .sample_events import SampleNewsProvider

        return SampleNewsProvider()
    if name == "finnhub":
        from .finnhub import FinnhubProvider

        return FinnhubProvider(provider_secret("finnhub", "API_KEY") or s.finnhub_api_key)
    if name == "none":
        return None
    raise ValueError(f"Unknown news provider {name!r}")


def calendar_provider(name: str = None):
    """Economic + earnings calendar provider."""
    s = get_settings()
    name = name or s.calendar_provider
    if name == "sample":
        from .sample_events import SampleCalendarProvider

        return SampleCalendarProvider()
    if name == "finnhub":
        from .finnhub import FinnhubProvider

        return FinnhubProvider(provider_secret("finnhub", "API_KEY") or s.finnhub_api_key)
    if name == "csv":
        from .csv_provider import CsvCalendarProvider

        return CsvCalendarProvider(s.csv_data_dir)
    if name == "none":
        return None
    raise ValueError(f"Unknown calendar provider {name!r}")
