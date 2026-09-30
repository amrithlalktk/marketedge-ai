"""Multi-timeframe trend agreement.

Daily data yields Weekly (completed weeks) and Daily states. When intraday
bars are supplied, 1H and 4H states are added. 4H/1H are informational for
swing setups: they are not part of the backtested rule set.
"""
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from . import indicators as ind


def trend_state(df: pd.DataFrame) -> str:
    if len(df) < 60:
        return "Insufficient data"
    c = df["close"]
    e20, e50 = ind.ema(c, 20), ind.ema(c, 50)
    h = ind.macd(c)["macd_hist"]
    last = -1
    if c.iloc[last] > e20.iloc[last] > e50.iloc[last] and h.iloc[last] > 0:
        return "Bullish"
    if c.iloc[last] < e20.iloc[last] < e50.iloc[last] and h.iloc[last] < 0:
        return "Bearish"
    return "Neutral"


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def analyze(daily: pd.DataFrame, direction: str, intraday_1h: Optional[pd.DataFrame] = None) -> Dict:
    weekly = _resample(daily, "W-FRI")
    # drop the in-progress week so weekly state matches the backtested (completed-week) rule
    if len(weekly) and weekly.index[-1] > daily.index[-1]:
        weekly = weekly.iloc[:-1]
    tfs: Dict[str, str] = {"Weekly": trend_state(weekly), "Daily": trend_state(daily)}
    if intraday_1h is not None and len(intraday_1h) > 0:
        tfs["4H"] = trend_state(_resample(intraday_1h, "4h"))
        tfs["1H"] = trend_state(intraday_1h)
    want = "Bullish" if direction == "LONG" else "Bearish"
    against = "Bearish" if direction == "LONG" else "Bullish"
    agree = sum(v == want for v in tfs.values())
    conflict = tfs["Weekly"] == against
    if conflict:
        alignment = "CONFLICT"
    elif agree == len(tfs):
        alignment = "STRONG"
    elif agree >= len(tfs) / 2:
        alignment = "MODERATE"
    else:
        alignment = "WEAK"
    return {"timeframes": tfs, "alignment": alignment, "priority": ["Weekly", "Daily", "4H"],
            "note": "Weekly uses completed weeks only. 4H/1H shown when intraday data is available and are not part of the backtested rules."}
