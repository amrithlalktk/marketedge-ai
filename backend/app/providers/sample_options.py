"""SAMPLE / SYNTHETIC NIFTY option chain — FOR DEVELOPMENT AND TESTS ONLY.

Premiums are Black-76 prices of a smile-shaped IV surface anchored to the
synthetic DEMO VIX; OI, volume and spreads are generated to look structurally
plausible (OI concentrated near the money and at round strikes; spreads widen
away from the money). Nothing here is real market data.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

from engine.options.pricing import IST, black76, forward, year_fraction

from .base import ChainSnapshot, DataMeta, OptionsDataProvider
from .sample import BENCHMARK, VIX_SYMBOL, get_universe

LOT_SIZE = 75  # sample value; real lot sizes come from the exchange contract master
STEP = 50
R, Q = 0.065, 0.012


def _expiries(as_of: date, weekday: int = 1) -> List[date]:
    """Weekly expiries on `weekday` (Tuesday) for 5 weeks + the last-Tuesday monthlies of the next 2 months."""
    d = as_of + timedelta(days=(weekday - as_of.weekday()) % 7)
    if d == as_of:
        d += timedelta(days=0)
    weeklies = [d + timedelta(weeks=i) for i in range(5)]
    monthlies = []
    for m in range(1, 3):
        y, mo = as_of.year + (as_of.month - 1 + m) // 12, (as_of.month - 1 + m) % 12 + 1
        last = date(y + (mo == 12), (mo % 12) + 1, 1) - timedelta(days=1)
        while last.weekday() != weekday:
            last -= timedelta(days=1)
        monthlies.append(last)
    return sorted(set(e for e in weeklies + monthlies if e >= as_of))


def synthetic_chain(spot: float, base_iv: float, as_of_dt: datetime, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    atm = round(spot / STEP) * STEP
    strikes = np.arange(atm - 70 * STEP, atm + 70 * STEP + 1, STEP)
    for exp in _expiries(as_of_dt.date()):
        T = max(year_fraction(as_of_dt, exp), 1 / 365 / 24)
        F = float(forward(spot, R, Q, T))
        m = np.log(strikes / F)
        term = 1 + 0.04 * np.log(max(T * 365, 1) / 30)
        iv = np.clip(base_iv * term * (1 - 1.1 * m + 9 * m ** 2), 0.05, 1.5)
        for opt in ("CE", "PE"):
            mid = black76(F, strikes, T, iv, R, opt == "CE")
            spread = np.maximum(0.1, mid * (0.004 + 4 * m ** 2)) + 0.05
            bid = np.maximum(np.round((mid - spread / 2) / 0.05) * 0.05, 0.05)
            ask = np.round((mid + spread / 2) / 0.05) * 0.05
            dist = np.abs(strikes - spot) / spot
            side_bias = np.where(strikes >= spot, 1.3, 0.7) if opt == "CE" else np.where(strikes <= spot, 1.3, 0.7)
            round_bump = np.where(strikes % 500 == 0, 1.8, np.where(strikes % 100 == 0, 1.25, 1.0))
            near_bump = 1.6 if (exp - as_of_dt.date()).days <= 7 else 1.0
            oi_lots = 60000 * np.exp(-dist / 0.018) * side_bias * round_bump * near_bump * rng.lognormal(0, 0.25, len(strikes))
            oi = np.round(oi_lots) * LOT_SIZE
            vol = np.round(oi_lots * rng.uniform(0.3, 1.6, len(strikes))) * LOT_SIZE
            oi_chg = np.round(oi * rng.normal(0.02, 0.12, len(strikes)) / LOT_SIZE) * LOT_SIZE
            ltp = np.maximum(np.round((mid * (1 + rng.normal(0, 0.004, len(strikes)))) / 0.05) * 0.05, 0.05)
            for k, b, a, l, v, o, oc in zip(strikes, bid, ask, ltp, vol, oi, oi_chg):
                rows.append((exp, float(k), opt, float(b), float(a), float(l), float(v), float(o), float(oc)))
    return pd.DataFrame(rows, columns=["expiry", "strike", "option_type", "bid", "ask", "ltp", "volume", "oi", "oi_change"])


class SampleOptionsProvider(OptionsDataProvider):
    name = "sample"
    is_sample = True

    def __init__(self, n_stocks: int = 60, today: Optional[date] = None):
        self.n_stocks, self.today = n_stocks, today

    def _u(self):
        return get_universe(self.today, self.n_stocks)

    def get_option_chain(self, underlying: str, as_of: Optional[date] = None) -> ChainSnapshot:
        if underlying != BENCHMARK:
            raise KeyError(f"Sample options exist only for {BENCHMARK}")
        u = self._u()
        idx = u.bars[BENCHMARK]
        ts = idx.index[-1] if as_of is None else idx.index[idx.index <= pd.Timestamp(as_of)][-1]
        spot = float(idx.loc[ts, "close"])
        vix = float(u.bars[VIX_SYMBOL].loc[ts, "close"])
        as_of_dt = datetime.combine(ts.date(), time(15, 30), tzinfo=IST)
        seed = int(hashlib.sha256(f"chain{ts.date()}".encode()).hexdigest()[:8], 16)
        chain = synthetic_chain(spot, vix / 100, as_of_dt, seed)
        meta = DataMeta(source="sample-synthetic", is_sample=True, as_of=as_of_dt.isoformat(), fetched_at=datetime.now(IST).isoformat(),
                        note="SAMPLE DATA — synthetic option chain for development. Not real market data.")
        return ChainSnapshot(BENCHMARK, as_of_dt, spot, LOT_SIZE, chain, meta)

    def get_iv_history(self, underlying: str) -> Optional[pd.Series]:
        return self._u().bars[VIX_SYMBOL]["close"] / 100  # synthetic ATM-IV proxy

    def health(self) -> dict:
        return {"provider": self.name, "ok": True, "is_sample": True}
