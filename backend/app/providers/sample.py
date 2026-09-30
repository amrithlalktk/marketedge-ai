"""SAMPLE / SYNTHETIC data provider — FOR DEVELOPMENT AND TESTS ONLY.

Generates deterministic, regime-switching synthetic OHLCV for a fictional
universe. Every symbol is prefixed `DEMO_` and every payload is flagged
`is_sample=True` so it can never be mistaken for real market data. No
fundamentals or earnings dates are fabricated.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .base import DataMeta, Instrument, MarketDataProvider

SOURCE = "sample-synthetic"

SECTORS = ["IT", "Banking", "Financial Services", "Pharma", "Auto", "FMCG", "Metals", "Energy", "Realty", "Infrastructure"]

INDICES = [
    ("DEMO_NIFTY50", "DEMO NIFTY 50 (synthetic)", 18000.0, 1.0),
    ("DEMO_BANKNIFTY", "DEMO BANK NIFTY (synthetic)", 42000.0, 1.25),
    ("DEMO_MIDCAP", "DEMO NIFTY MIDCAP (synthetic)", 30000.0, 1.2),
    ("DEMO_SMALLCAP", "DEMO NIFTY SMALLCAP (synthetic)", 9000.0, 1.4),
]
VIX_SYMBOL = "DEMO_INDIAVIX"
BENCHMARK = "DEMO_NIFTY50"

_REGIMES = {  # daily drift, daily vol
    "bull": (0.0007, 0.0085),
    "range": (0.0, 0.008),
    "bear": (-0.0007, 0.014),
    "crash": (-0.004, 0.028),
}
_TRANSITIONS = {
    "bull": {"bull": 0.985, "range": 0.01, "bear": 0.004, "crash": 0.001},
    "range": {"bull": 0.012, "range": 0.975, "bear": 0.012, "crash": 0.001},
    "bear": {"bull": 0.006, "range": 0.012, "bear": 0.978, "crash": 0.004},
    "crash": {"bull": 0.05, "range": 0.05, "bear": 0.1, "crash": 0.8},
}


def _seed(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)


def _last_session(today: date) -> pd.Timestamp:
    d = pd.Timestamp(today)
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d


SAMPLE_START = pd.Timestamp("2018-11-01")  # fixed: a new day appends a bar without changing history


class SampleUniverse:
    """Builds the whole synthetic universe once so market/sector factors are shared.
    Prefix-stable: generating for a later `today` only appends bars (see _synth)."""

    def __init__(self, n_stocks: int = 60, years: int = 8, today: Optional[date] = None, seed: int = 42):
        from . import _synth as sy

        self.today = today or date.today()
        end = _last_session(self.today)
        self.dates = pd.bdate_range(start=SAMPLE_START, end=end)
        n = len(self.dates)
        mkt, states = sy.regime_market(f"nse-mkt-{seed}", n)
        self.market_ret, self.states = mkt, states
        self.sector_ret = {}
        for s in SECTORS:
            shocks = sy.draw(f"sector-{s}-drift", "normal", n, 0, 0.00006)
            drift = np.zeros(n)
            for i in range(1, n):
                drift[i] = 0.995 * drift[i - 1] + shocks[i]
            self.sector_ret[s] = drift + sy.draw(f"sector-{s}-noise", "normal", n, 0, 0.005)

        self.instruments: List[Instrument] = []
        self.bars: Dict[str, pd.DataFrame] = {}
        for sym, name, base, beta in INDICES:
            self.bars[sym] = sy.ohlc(sym, self.dates, beta * mkt, base, 0.25 * 0.009, 0)
            self.instruments.append(Instrument(sym, name, "INDEX", "NSE", "INR", None, is_index=True, is_sample=True))
        # causal realised-vol proxy (no backfill: the first bars use an expanding window)
        vix = 100 * np.sqrt(252) * pd.Series(mkt, index=self.dates).rolling(20, min_periods=2).std().fillna(0.009) * 1.1
        vix = vix.ewm(span=5, adjust=False).mean()
        self.bars[VIX_SYMBOL] = pd.DataFrame({"open": vix, "high": vix * 1.03, "low": vix * 0.97, "close": vix, "volume": 0.0}, index=self.dates)
        self.instruments.append(Instrument(VIX_SYMBOL, "DEMO INDIA VIX (synthetic)", "INDEX", "NSE", "INR", None, is_index=True, is_sample=True))

        for k in range(n_stocks):
            sym = f"DEMO_{k + 1:03d}"
            sector = SECTORS[k % len(SECTORS)]
            beta, idio = sy.scalar(sym + "-beta", 0.7, 1.4), sy.scalar(sym + "-idio", 0.011, 0.022)
            r = beta * mkt + self.sector_ret[sector] + sy.trend_episodes(sym, n, 0.0012) + sy.draw(sym + "-idio", "normal", n, 0, idio)
            df = sy.ohlc(sym, self.dates, r, sy.scalar(sym + "-px", 80, 3000), idio, sy.scalar(sym + "-volbase", 2e5, 5e6))
            listed, delisted = None, None
            if k >= n_stocks - 3:  # a few instruments delisted mid-history (fixed dates) to exercise survivorship handling
                cut = min(int(2016 * sy.scalar(sym + "-delist", 0.4, 0.8)), n - 1)
                delisted = str(df.index[cut].date())
                df = df.iloc[: cut + 1]
            if k in (5, 17):  # late listings (fixed date)
                start = min(600, n - 1)
                listed = str(df.index[start].date())
                df = df.iloc[start:]
            self.bars[sym] = df
            self.instruments.append(Instrument(sym, f"Demo Company {k + 1:03d} (synthetic)", "EQUITY", "NSE", "INR", sector,
                                               listed_on=listed, delisted_on=delisted, is_sample=True, lot_size=1))


_CACHE: Dict[tuple, SampleUniverse] = {}


def get_universe(today: Optional[date] = None, n_stocks: int = 60) -> SampleUniverse:
    key = (today or date.today(), n_stocks)
    if key not in _CACHE:
        _CACHE[key] = SampleUniverse(n_stocks=n_stocks, today=key[0])
    return _CACHE[key]


class SampleMarketDataProvider(MarketDataProvider):
    name = "sample"
    is_sample = True

    def __init__(self, n_stocks: int = 60, today: Optional[date] = None):
        self.n_stocks, self.today = n_stocks, today

    def _u(self) -> SampleUniverse:
        return get_universe(self.today, self.n_stocks)

    def list_instruments(self, market: str = "NSE") -> List[Instrument]:
        return list(self._u().instruments)

    def get_ohlcv(self, symbol: str, interval: str = "1d", start: Optional[date] = None, end: Optional[date] = None):
        if interval == "5m":
            df = intraday_5m(self._u(), symbol)
        elif interval != "1d":
            raise NotImplementedError("Sample provider serves 1d bars, and 5m bars for the benchmark index")
        else:
            df = self._u().bars.get(symbol)
        if df is None:
            raise KeyError(symbol)
        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        meta = DataMeta(source=SOURCE, is_sample=True, as_of=str(df.index[-1].date()) if len(df) else None,
                        fetched_at=datetime.now(timezone.utc).isoformat(), delayed=False,
                        note="SAMPLE DATA — synthetic prices for development. Not real market data.")
        return df.copy(), meta

    def health(self) -> dict:
        return {"provider": self.name, "ok": True, "is_sample": True, "note": "Synthetic development data"}


def intraday_5m(u: SampleUniverse, symbol: str, sessions: int = 30) -> pd.DataFrame:
    """Synthetic 5-minute bars (09:15–15:30 IST) for the benchmark only, consistent with
    each day's synthetic O/H/L/C: a Brownian bridge from open to close, monotonically
    rescaled so the session high/low match the daily bar. Volume is a U-shaped proxy
    (indices have no traded volume; real VWAP would use futures volume)."""
    if symbol != BENCHMARK:
        raise KeyError(f"Sample intraday bars exist only for {BENCHMARK}")
    daily = u.bars[symbol].iloc[-sessions:]
    frames = []
    n = 75
    shape = 1.6 - np.sin(np.linspace(0, np.pi, n))  # heavier at open/close
    for ts, d in daily.iterrows():
        rs = np.random.default_rng(_seed(f"{symbol}{ts.date()}"))
        w = np.concatenate([[0], np.cumsum(rs.standard_normal(n))])
        t = np.linspace(0, 1, n + 1)
        bridge = d.open + (d.close - d.open) * t + (w - t * w[-1]) * (abs(d.high - d.low) / 6)
        top, bot = max(d.open, d.close), min(d.open, d.close)
        hi_b, lo_b = bridge.max(), bridge.min()
        path = bridge.copy()
        if hi_b > top:
            path = np.where(path > top, top + (path - top) * (d.high - top) / (hi_b - top), path)
        if lo_b < bot:
            path = np.where(path < bot, bot - (bot - path) * (bot - d.low) / (bot - lo_b), path)
        opens, closes = path[:-1], path[1:]
        wig = np.abs(rs.normal(0, abs(d.high - d.low) / 60, n))
        highs = np.minimum(np.maximum(opens, closes) + wig, d.high)
        lows = np.maximum(np.minimum(opens, closes) - wig, d.low)
        highs[np.argmax(path[1:])] = d.high if hi_b > top else highs[np.argmax(path[1:])]
        lows[np.argmin(path[1:])] = d.low if lo_b < bot else lows[np.argmin(path[1:])]
        idx = pd.date_range(pd.Timestamp(ts.date()) + pd.Timedelta(hours=9, minutes=20), periods=n, freq="5min")
        frames.append(pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                                    "volume": np.round(1e5 * shape * rs.lognormal(0, 0.3, n))}, index=idx))
    return pd.concat(frames).round(2)
