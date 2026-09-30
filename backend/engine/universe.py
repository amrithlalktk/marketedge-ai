"""Point-in-time universes: which instruments were "the top N" on each date.

Ranking the universe by *today's* volume and replaying history on it is survivorship bias:
the coins that crashed, faded or were delisted are missing, and the ones that became big are
over-represented. Here membership on date t is decided only from data up to t-1:

    traded value = close × volume, averaged over `window` bars (at least `min_history`),
    shifted one bar, ranked across every instrument that had data that day → top N.

USD-pegged behaviour (stablecoins, which a name list can never keep up with) is excluded
per date: price within 10% of 1.0 and a median daily high/low range under 0.5%.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

import pandas as pd


def pegged_mask(df: pd.DataFrame, window: int = 30) -> pd.Series:
    rng = (df["high"] / df["low"] - 1).rolling(window, min_periods=min(10, window)).median()
    near_one = df["close"].between(0.9, 1.1)
    return (near_one & (rng < 0.005)).astype(bool).shift(1, fill_value=False)


def top_n_membership(bars: Dict[str, pd.DataFrame], n: int, window: int = 30, min_history: int = 30,
                     exclude: Iterable[str] = (), exclude_pegged: bool = True) -> Dict[str, pd.Series]:
    """{symbol: bool Series on that symbol's own dates} — True where it ranked in the top `n`."""
    skip = set(exclude)
    cols = {}
    for sym, df in bars.items():
        if sym in skip or df is None or df.empty:
            continue
        tv = (df["close"] * df["volume"]).rolling(window, min_periods=min_history).mean().shift(1)
        if exclude_pegged:
            tv = tv.mask(pegged_mask(df, window))
        cols[sym] = tv
    if not cols:
        return {}
    panel = pd.DataFrame(cols)
    member = panel.rank(axis=1, ascending=False, method="first") <= n  # NaN (no data / too new / pegged) → False
    return {sym: member[sym].reindex(bars[sym].index, fill_value=False).astype(bool) for sym in cols}


def current_members(membership: Dict[str, pd.Series], as_of: Optional[pd.Timestamp] = None) -> set:
    """Symbols that are members on the latest date of the panel (a delisted symbol's series ends earlier)."""
    if not membership:
        return set()
    last = as_of or max(s.index[-1] for s in membership.values() if len(s))
    return {sym for sym, s in membership.items() if len(s) and s.index[-1] == last and bool(s.iloc[-1])}


def membership_summary(membership: Dict[str, pd.Series], delisted: Iterable[str] = ()) -> dict:
    ever = {sym for sym, s in membership.items() if s.any()}
    dl = set(delisted)
    return {"ever_members": len(ever), "delisted_members": len(ever & dl), "current_members": len(current_members(membership)),
            "method": "top-N by trailing 30-bar traded value, decided from data up to the previous bar"}
