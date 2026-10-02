"""SAMPLE / SYNTHETIC crypto data — DEVELOPMENT AND TESTS ONLY.

Every symbol is prefixed DEMO_ and flagged is_sample. Crypto derivatives series
(funding, OI, long/short) are synthetic too.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import _synth as sy
from .base import DataMeta, Instrument, MarketDataProvider

SOURCE = "sample-synthetic"

STARTS = {"weekdays": pd.Timestamp("2018-11-01"), "24x7": pd.Timestamp("2020-10-01")}  # fixed → prefix-stable history


def _calendar(today: date, years: float, calendar: str) -> pd.DatetimeIndex:
    if calendar == "24x7":
        end = pd.Timestamp(today) - pd.Timedelta(days=1)  # last COMPLETE UTC day
        return pd.date_range(start=STARTS["24x7"], end=end, freq="D")
    end = pd.Timestamp(today)
    while end.weekday() >= 5:
        end -= pd.Timedelta(days=1)
    return pd.bdate_range(start=STARTS["weekdays"], end=end)



@dataclass
class _Built:
    instruments: List[Instrument]
    bars: Dict[str, pd.DataFrame]
    derivatives: Dict[str, pd.DataFrame]



_COINS = [("BTC", "L1", 60000, 1.2e12), ("ETH", "L1", 3000, 3.8e11), ("SOL", "L1", 150, 7e10), ("XRP", "Payments", 0.6, 3.3e10),
          ("BNB", "Exchange", 580, 8.5e10), ("ADA", "L1", 0.45, 1.6e10), ("AVAX", "L1", 35, 1.3e10), ("LINK", "Oracle", 15, 9e9),
          ("DOGE", "Meme", 0.15, 2.1e10), ("DOT", "L1", 7, 9.5e9), ("TRX", "L1", 0.12, 1.05e10), ("LTC", "Payments", 80, 6e9),
          ("MATIC", "L2", 0.7, 6.5e9), ("UNI", "DeFi", 8, 5e9), ("ATOM", "L1", 8, 3e9), ("AAVE", "DeFi", 110, 1.6e9),
          ("ARB", "L2", 0.9, 3e9), ("OP", "L2", 2, 2.2e9), ("SHIB", "Meme", 0.00002, 1.2e10), ("ILLIQ", "Meme", 0.02, 4e7)]


def _crypto_market(today: date) -> _Built:
    dates = _calendar(today, 6, "24x7")
    n = len(dates)
    btc_r, _ = sy.regime_market("crypto-mkt", n, 3.2)
    inst, bars, deriv = [], {}, {}
    for tk, sec, px, mcap in _COINS:
        s = f"DEMO_{tk}"
        if tk == "BTC":
            r = btc_r
            vol = 0.03
        else:
            beta, idio = sy.scalar(s + "-beta", 1.0, 1.6), sy.scalar(s + "-idio", 0.025, 0.05)
            r = beta * btc_r + sy.trend_episodes(s, n, 0.003) + sy.draw(s + "-idio", "normal", n, 0, idio)
            vol = idio
        anchor = min(1800, n - 1)  # a fixed bar inside every history: price there ≈ the nominal level (prefix-stable)
        start_px = px / float(np.exp(np.cumsum(r)[anchor]))
        usd_vol = (mcap * sy.scalar(s + "-turnover", 0.02, 0.06)) if tk != "ILLIQ" else 5e5
        df = sy.ohlc(s, dates, r, start_px, vol, usd_vol / px, 8 if px < 1 else 2)
        bars[s] = df
        inst.append(Instrument(s, f"Demo {tk} (synthetic)", "CRYPTO", "CRYPTO", "USD", sec, is_sample=True,
                               extra={"market_cap_usd": float(mcap), "quote": "USDT", "base": tk,
                                      "spread_bps": float(sy.scalar(s + "-spread", 1, 6) if tk != "ILLIQ" else 80)}))
        # synthetic perpetual-futures context (funding follows recent momentum; OI trends with price)
        ret7 = pd.Series(np.log(df["close"])).diff(7).fillna(0).to_numpy()
        funding = np.clip(0.0001 + 0.002 * ret7 + sy.draw(s + "-fund", "normal", n, 0, 0.00008), -0.002, 0.003)
        oi = mcap * 0.02 * np.exp(np.cumsum(0.6 * r + sy.draw(s + "-oi", "normal", n, 0, 0.01)) * 0.5)
        ls = np.clip(1.2 + 3 * ret7 + sy.draw(s + "-ls", "normal", n, 0, 0.1), 0.4, 4.0)
        if tk != "ILLIQ":
            deriv[s] = pd.DataFrame({"funding_rate": funding, "open_interest": oi, "long_short_ratio": ls}, index=dates).iloc[-60:]
    return _Built(inst, bars, deriv)



_CACHE: Dict[Tuple[str, date], _Built] = {}


def build(market: str, today: Optional[date] = None) -> _Built:
    today = today or date.today()
    key = (market, today)
    if key not in _CACHE:
        if market != "CRYPTO":
            raise ValueError(f"No sample data for market {market!r}")
        _CACHE[key] = _crypto_market(today)
    return _CACHE[key]


class SampleCryptoProvider(MarketDataProvider):
    name = "sample"
    is_sample = True

    def __init__(self, market: str, today: Optional[date] = None):
        self.market, self.today = market, today

    def list_instruments(self, market: str = None) -> List[Instrument]:
        return list(build(market or self.market, self.today).instruments)

    def get_ohlcv(self, symbol: str, interval: str = "1d", start: Optional[date] = None, end: Optional[date] = None):
        if interval != "1d":
            raise NotImplementedError("Sample crypto provider serves daily bars only")
        df = build(self.market, self.today).bars.get(symbol)
        if df is None:
            raise KeyError(symbol)
        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        return df.copy(), DataMeta(source=SOURCE, is_sample=True, as_of=str(df.index[-1].date()) if len(df) else None,
                                   fetched_at=datetime.now(timezone.utc).isoformat(), note="SAMPLE DATA — synthetic prices. Not real market data.")

    def get_derivatives(self, symbol: str) -> Optional[pd.DataFrame]:
        d = build(self.market, self.today).derivatives.get(symbol)
        return None if d is None else d.copy()

    def health(self) -> dict:
        return {"provider": f"sample:{self.market}", "ok": True, "is_sample": True}
