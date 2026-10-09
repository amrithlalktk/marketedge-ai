"""Candidate strategies (version 2) — tested by the diagnostics before any of them may go live.

They follow the "critical patch" definitions and are kept OUT of `strategies.STRATEGIES`, so the daily scan
is unaffected until a candidate passes the acceptance rules out of sample. Same contract as the live rules:
evaluated on the close of bar t with data known at t; entry at the next open; levels from `levels.compute_levels`.
"""
from __future__ import annotations

from typing import Dict

import pandas as pd

from .strategies import StrategySpec, _breakout_volume


def add_relative_strength(f: pd.DataFrame, bench_close: pd.Series) -> pd.DataFrame:
    """rsN = stock N-day return − benchmark N-day return (both known at t). Missing benchmark days are forward-filled."""
    b = bench_close.reindex(f.index).ffill()
    for n in (20, 60, 120):
        f[f"rs{n}"] = f["close"].pct_change(n) - b.pct_change(n)
    return f


def _ema50_rising(f: pd.DataFrame) -> pd.Series:
    return f["ema50"] > f["ema50"].shift(10)


def _breakout_retest(f: pd.DataFrame) -> pd.Series:
    """A: a volume breakout 2–6 bars ago, price comes back to the broken level, holds it and closes up."""
    bo = _breakout_volume(f) & _ema50_rising(f)
    level = f["hh20_prior"].where(bo).ffill(limit=6)
    recent = bo.shift(2).astype(float).rolling(5, min_periods=1).max().fillna(0).astype(bool)
    return (recent & ~bo & (f["low"] <= level + 0.5 * f["atr"]) & (f["close"] > level)
            & (f["close"] > f["open"]) & (f["close_loc"] >= 0.5))


def _trend_pullback_v2(f: pd.DataFrame) -> pd.Series:
    """B: rising 50 EMA above the 200 EMA, a pullback to the 20 or 50 EMA, then a bullish reversal bar."""
    touched20 = (f["low"] <= f["ema20"] + 0.3 * f["atr"]).astype(float).rolling(3).max().fillna(0).astype(bool)
    touched50 = (f["low"] <= f["ema50"] + 0.3 * f["atr"]).astype(float).rolling(3).max().fillna(0).astype(bool)
    return ((f["close"] > f["ema50"]) & _ema50_rising(f) & (f["ema50"] > f["ema200"])
            & (touched20 | touched50)
            & (f["close"] > f["high"].shift(1)) & (f["close"] > f["open"]) & (f["close_loc"] >= 0.6)
            & f["rsi"].between(40, 65) & (f["weekly_trend"] >= 0))


def _rs_leader(f: pd.DataFrame) -> pd.Series:
    """C: outperforming NIFTY over 60 and 120 days with improving 20-day relative strength, in an uptrend,
    triggered by a close above the prior 10-day high on above-average volume. (Sector confirmation needs a
    stock→sector map, which the data source does not provide.)"""
    if "rs60" not in f:
        return pd.Series(False, index=f.index)
    hh10 = f["high"].rolling(10, min_periods=10).max().shift(1)
    return ((f["rs60"] > 0) & (f["rs120"] > 0) & (f["rs20"] > f["rs20"].shift(5))
            & (f["close"] > f["ema50"]) & (f["ema50"] > f["ema200"])
            & (f["close"] > hh10) & (f["vol_ratio20"] >= 1.2) & (f["close_loc"] >= 0.6))


def _support_reversal_v2(f: pd.DataFrame) -> pd.Series:
    """D: a rejection at a confirmed support (long lower wick, strong close) on volume, momentum turning up,
    higher-low structure above the 200 EMA. Never just 'RSI oversold'."""
    rng = (f["high"] - f["low"]).where(lambda x: x > 0)
    lower_wick = (f[["open", "close"]].min(axis=1) - f["low"]) / rng
    return (((f["low"] - f["swing_low"]).abs() <= 0.5 * f["atr"]) & (f["close"] > f["swing_low"])
            & (lower_wick >= 0.4) & (f["close_loc"] >= 0.6) & (f["close"] > f["open"])
            & (f["vol_ratio20"] >= 1.2) & (f["rsi"] > f["rsi"].shift(1))
            & f["higher_low"] & (f["close"] > f["ema200"]))


CANDIDATES: Dict[str, StrategySpec] = {
    s.id: s
    for s in [
        StrategySpec("breakout_retest", "Breakout Retest (v2)", "LONG", "Breakout + Volume Confirmation",
                     "A volume breakout above the 20-day high, then a pullback that holds the broken level and closes up.",
                     ["Volume breakout 2–6 bars ago (rising 50 EMA)", "Low within 0.5 ATR of the broken level, close above it",
                      "Bullish close (top half of range)"], _breakout_retest),
        StrategySpec("trend_pullback_v2", "Trend Pullback (v2)", "LONG", "Trend Continuation (Pullback)",
                     "Rising 50 EMA above the 200 EMA; pullback to the 20/50 EMA resolved by a bullish reversal bar.",
                     ["Close > 50 EMA, 50 EMA rising (10 bars), 50 EMA > 200 EMA", "Low within 0.3 ATR of the 20 or 50 EMA (3 bars)",
                      "Close > prior high, bullish bar closing in the top 40%", "RSI 40–65", "Weekly trend not bearish"], _trend_pullback_v2),
        StrategySpec("rs_leader", "Relative-Strength Leader (v2)", "LONG", "Relative Strength",
                     "Outperforming NIFTY over 60 and 120 days with improving 20-day relative strength; breakout of the 10-day high.",
                     ["60- and 120-day return above NIFTY's", "20-day relative strength improving", "Close > 50 EMA > 200 EMA",
                      "Close > prior 10-day high on ≥ 1.2× volume"], _rs_leader),
        StrategySpec("support_reversal_v2", "Support Reversal (v2)", "LONG", "Support Bounce",
                     "Rejection of a confirmed support with a long lower wick and volume, momentum turning up, above the 200 EMA.",
                     ["Low within 0.5 ATR of the last confirmed swing low, close above it", "Lower wick ≥ 40% of the range, close in the top 40%",
                      "Volume ≥ 1.2× average", "RSI rising", "Higher low, close > 200 EMA"], _support_reversal_v2),
    ]
}
