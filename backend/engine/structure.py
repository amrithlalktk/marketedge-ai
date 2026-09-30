"""Market structure: swing pivots, support/resistance and trend structure.

A pivot high at bar i needs `right` later bars to be confirmed, so it only
becomes *known* at bar i + right. Every series here is aligned to the
confirmation bar, never to the pivot bar, which keeps them look-ahead free.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd


def confirmed_pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    ph = np.full(n, np.nan)  # value placed at the confirmation bar
    pl = np.full(n, np.nan)
    ph_bar = np.full(n, -1)
    pl_bar = np.full(n, -1)
    for i in range(left, n - right):
        window_h = h[i - left : i + right + 1]
        window_l = l[i - left : i + right + 1]
        if h[i] == window_h.max() and (window_h == h[i]).sum() == 1:
            ph[i + right], ph_bar[i + right] = h[i], i
        if l[i] == window_l.min() and (window_l == l[i]).sum() == 1:
            pl[i + right], pl_bar[i + right] = l[i], i
    return pd.DataFrame({"pivot_high": ph, "pivot_low": pl, "pivot_high_bar": ph_bar, "pivot_low_bar": pl_bar}, index=df.index)


def add_structure(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
    """Adds last/previous confirmed swing highs & lows and HH/HL/LH/LL flags."""
    out = df.copy()
    piv = confirmed_pivots(out, left, right)
    out["pivot_high"], out["pivot_low"] = piv["pivot_high"], piv["pivot_low"]
    sh = piv["pivot_high"].dropna()
    sl = piv["pivot_low"].dropna()
    out["swing_high"] = sh.reindex(out.index).ffill()
    out["swing_low"] = sl.reindex(out.index).ffill()
    out["prev_swing_high"] = sh.shift(1).reindex(out.index).ffill()
    out["prev_swing_low"] = sl.shift(1).reindex(out.index).ffill()
    out["higher_high"] = out["swing_high"] > out["prev_swing_high"]
    out["higher_low"] = out["swing_low"] > out["prev_swing_low"]
    out["lower_high"] = out["swing_high"] < out["prev_swing_high"]
    out["lower_low"] = out["swing_low"] < out["prev_swing_low"]
    return out


@dataclass
class Level:
    price: float
    kind: str  # support | resistance
    touches: int
    last_touch: str


def sr_levels(df: pd.DataFrame, t: int, lookback: int = 250, tol_atr: float = 0.5) -> List[Level]:
    """Cluster confirmed pivots known at bar t into support/resistance levels.

    `df` must contain the output of `add_structure` and an `atr` column.
    """
    start = max(0, t - lookback)
    window = df.iloc[start : t + 1]
    close = float(df["close"].iloc[t])
    a = float(df["atr"].iloc[t]) if not np.isnan(df["atr"].iloc[t]) else close * 0.02
    prices = []
    for col in ("pivot_high", "pivot_low"):
        s = window[col].dropna()
        prices.extend((float(p), str(ix.date()) if hasattr(ix, "date") else str(ix)) for ix, p in s.items())
    prices.sort()
    clusters: List[List] = []
    for p, when in prices:
        if clusters and p - clusters[-1][-1][0] <= tol_atr * a:
            clusters[-1].append((p, when))
        else:
            clusters.append([(p, when)])
    levels = []
    for c in clusters:
        px = float(np.mean([p for p, _ in c]))
        levels.append(Level(price=round(px, 4), kind="resistance" if px > close else "support", touches=len(c), last_touch=max(w for _, w in c)))
    return levels


def nearest_levels(levels: List[Level], price: float):
    supports = sorted([lv for lv in levels if lv.price < price], key=lambda x: -x.price)
    resistances = sorted([lv for lv in levels if lv.price > price], key=lambda x: x.price)
    return supports, resistances
