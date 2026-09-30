"""Rule-based market regime classification on a benchmark index.

Regimes: Strong Bull, Weak Bull, Range, Bear, Panic/Selloff, Recovery, with a
separate volatility state (High / Normal / Low). Each bar's label uses only
data up to that bar, so the series can be joined to historical trades to
build regime-conditional statistics.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators as ind

REGIMES = ["Strong Bull", "Weak Bull", "Range", "Bear", "Panic/Selloff", "Recovery"]
FAMILY = {"Strong Bull": "bull", "Weak Bull": "bull", "Recovery": "bull", "Range": "range", "Bear": "bear", "Panic/Selloff": "stress"}


def classify(index_df: pd.DataFrame, vix: Optional[pd.Series] = None, breadth50: Optional[pd.Series] = None) -> pd.DataFrame:
    c, h, l = index_df["close"], index_df["high"], index_df["low"]
    e50, e200 = ind.ema(c, 50), ind.ema(c, 200)
    adx = ind.adx(h, l, c)["adx"]
    atrp = 100 * ind.atr(h, l, c) / c
    atr_rank = ind.rolling_percentile_rank(atrp, 252)
    dd = c / c.rolling(252, min_periods=60).max() - 1
    ret5, ret10, ret20, ret50 = c.pct_change(5), c.pct_change(10), c.pct_change(20), c.pct_change(50)
    recent_dd = dd.rolling(60, min_periods=20).min()
    vix = vix.reindex(c.index).ffill() if vix is not None else pd.Series(np.nan, index=c.index)
    b50 = breadth50.reindex(c.index).ffill() if breadth50 is not None else pd.Series(np.nan, index=c.index)

    labels, vol_state = [], []
    for i in range(len(c)):
        if np.isnan(e200.iloc[i]):
            labels.append(None)
            vol_state.append(None)
            continue
        vs = "High" if atr_rank.iloc[i] >= 0.85 or (vix.iloc[i] >= 22) else ("Low" if atr_rank.iloc[i] <= 0.15 else "Normal")
        vol_state.append(vs)
        panic = ret10.iloc[i] <= -0.07 or (ret5.iloc[i] <= -0.04 and vs == "High") or (vix.iloc[i] >= 28)
        if panic:
            labels.append("Panic/Selloff")
            continue
        above50, above200, stacked = c.iloc[i] > e50.iloc[i], c.iloc[i] > e200.iloc[i], e50.iloc[i] > e200.iloc[i]
        if above50 and not stacked and recent_dd.iloc[i] <= -0.10 and ret20.iloc[i] >= 0.04:
            labels.append("Recovery")
        elif adx.iloc[i] < 18 and abs(ret50.iloc[i]) < 0.04:
            labels.append("Range")
        elif above50 and above200 and stacked and adx.iloc[i] >= 22 and e200.pct_change(20).iloc[i] > 0 and (np.isnan(b50.iloc[i]) or b50.iloc[i] >= 55):
            labels.append("Strong Bull")
        elif above200 and stacked:
            labels.append("Weak Bull")
        elif not above200 and not stacked:
            labels.append("Bear")
        else:
            labels.append("Range")
    out = pd.DataFrame({
        "regime": labels, "volatility": vol_state, "adx": adx, "atr_pct": atrp, "atr_rank": atr_rank,
        "drawdown": dd, "ret20": ret20, "ema50": e50, "ema200": e200, "vix": vix, "breadth50": b50,
    }, index=c.index)
    out["family"] = out["regime"].map(FAMILY)
    return out


def describe(reg: pd.DataFrame, index_close: pd.Series) -> Dict:
    last = reg.dropna(subset=["regime"]).iloc[-1]
    ts = reg.dropna(subset=["regime"]).index[-1]
    price = float(index_close.loc[ts])
    reasons: List[str] = [
        f"Index {'above' if price > last['ema50'] else 'below'} 50 EMA ({last['ema50']:.0f}) and {'above' if price > last['ema200'] else 'below'} 200 EMA ({last['ema200']:.0f})",
        f"ADX {last['adx']:.1f} ({'trending' if last['adx'] >= 22 else 'weak/no trend'})",
        f"ATR% percentile {100 * last['atr_rank']:.0f} (volatility {last['volatility']})",
        f"20-day return {100 * last['ret20']:.1f}%, drawdown from 52w high {100 * last['drawdown']:.1f}%",
    ]
    if not np.isnan(last["vix"]):
        reasons.append(f"Volatility index {last['vix']:.1f}")
    if not np.isnan(last["breadth50"]):
        reasons.append(f"{last['breadth50']:.0f}% of universe above 50 DMA")
    favorability = {"Strong Bull": 85, "Weak Bull": 65, "Recovery": 60, "Range": 45, "Bear": 25, "Panic/Selloff": 10}[last["regime"]]
    if last["volatility"] == "High":
        favorability -= 10
    return {
        "as_of": str(ts.date()), "regime": last["regime"], "family": last["family"], "volatility": last["volatility"],
        "long_favorability": max(0, favorability), "short_favorability": max(0, 100 - favorability - 10),
        "reasons": reasons,
    }


def regime_score_for(direction: str, regime: Optional[str], vol: Optional[str]) -> float:
    if regime is None:
        return 50.0
    long_map = {"Strong Bull": 100, "Weak Bull": 75, "Recovery": 70, "Range": 45, "Bear": 15, "Panic/Selloff": 5}
    short_map = {"Strong Bull": 5, "Weak Bull": 20, "Recovery": 25, "Range": 45, "Bear": 90, "Panic/Selloff": 60}
    s = (long_map if direction == "LONG" else short_map)[regime]
    return float(s - (10 if vol == "High" else 0))
