"""Feature pipeline: indicators + structure + higher-timeframe context + pattern flags."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from .structure import add_structure


def weekly_trend_completed(df: pd.DataFrame) -> pd.Series:
    """Weekly trend (+1 bull / -1 bear / 0 neutral) from COMPLETED weeks only.

    A daily bar on Wednesday sees the trend as of last Friday's close. This is
    the same definition used live and in backtests, so filters are consistent.
    """
    wk = df[["open", "high", "low", "close", "volume"]].resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    e10, e30 = ind.ema(wk["close"], 10), ind.ema(wk["close"], 30)
    m = ind.macd(wk["close"])["macd_hist"]
    trend = pd.Series(0, index=wk.index, dtype=float)
    trend[(wk["close"] > e10) & (e10 > e30) & (m > 0)] = 1
    trend[(wk["close"] < e10) & (e10 < e30) & (m < 0)] = -1
    trend[e30.isna()] = np.nan
    # Week ending Friday F is only complete after F's close -> usable from the next bar.
    shifted = trend.copy()
    shifted.index = shifted.index + pd.Timedelta(days=1)
    return shifted.reindex(df.index, method="ffill")


def add_patterns(f: pd.DataFrame) -> pd.DataFrame:
    a = f["atr"]
    c, o, h, l = f["close"], f["open"], f["high"], f["low"]
    f["pat_breakout"] = c > f["hh20_prior"]
    f["pat_breakdown"] = c < f["ll20_prior"]
    rng15 = (h.rolling(15).max() - l.rolling(15).min()).shift(1)
    f["pat_consolidation"] = (rng15 / a.shift(1)) < 4.0
    f["pat_gap_up"] = o > h.shift(1)
    f["pat_gap_down"] = o < l.shift(1)
    f["pat_gap_fill_down"] = f["pat_gap_up"].shift(1, fill_value=False) & (l <= h.shift(2))
    near = 0.5 * a
    f["pat_double_bottom"] = ((f["swing_low"] - f["prev_swing_low"]).abs() < near) & (c > f["swing_high"]) & (c.shift(1) <= f["swing_high"])
    f["pat_double_top"] = ((f["swing_high"] - f["prev_swing_high"]).abs() < near) & (c < f["swing_low"]) & (c.shift(1) >= f["swing_low"])
    pole = (c.shift(5) - c.shift(15)) / a
    flag_range = (h.rolling(5).max() - l.rolling(5).min()).shift(1) / a
    f["pat_bull_flag"] = (pole > 3) & (flag_range < 2.0) & (c > h.rolling(5).max().shift(1))
    f["pat_bear_flag"] = (pole < -3) & (flag_range < 2.0) & (c < l.rolling(5).min().shift(1))
    f["pat_support_bounce"] = ((l - f["swing_low"]).abs() < near) & (f["close_loc"] > 0.6) & (c > o)
    f["pat_resistance_rejection"] = ((h - f["swing_high"]).abs() < near) & (f["close_loc"] < 0.4) & (c < o)
    stacked_up = (f["ema20"] > f["ema50"]) & (f["ema50"] > f["ema200"])
    stacked_dn = (f["ema20"] < f["ema50"]) & (f["ema50"] < f["ema200"])
    f["pat_trend_continuation"] = (stacked_up & f["pat_breakout"]) | (stacked_dn & f["pat_breakdown"])
    f["pat_trend_reversal_up"] = f["lower_low"].shift(5, fill_value=False) & (c > f["ema50"]) & (c.shift(1) <= f["ema50"].shift(1))
    f["pat_trend_reversal_down"] = f["higher_high"].shift(5, fill_value=False) & (c < f["ema50"]) & (c.shift(1) >= f["ema50"].shift(1))
    return f


PATTERN_LABELS = {
    "pat_breakout": "Breakout above 20-bar high",
    "pat_breakdown": "Breakdown below 20-bar low",
    "pat_consolidation": "Prior consolidation (tight 15-bar range)",
    "pat_gap_up": "Gap up",
    "pat_gap_down": "Gap down",
    "pat_gap_fill_down": "Gap fill after gap up",
    "pat_double_bottom": "Double bottom confirmation",
    "pat_double_top": "Double top confirmation",
    "pat_bull_flag": "Bull flag breakout",
    "pat_bear_flag": "Bear flag breakdown",
    "pat_support_bounce": "Support bounce",
    "pat_resistance_rejection": "Resistance rejection",
    "pat_trend_continuation": "Trend continuation",
    "pat_trend_reversal_up": "Potential trend reversal (up)",
    "pat_trend_reversal_down": "Potential trend reversal (down)",
}


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """df: OHLCV with a DatetimeIndex (ascending). Returns the enriched frame."""
    from .patterns_geo import detect as detect_geo

    df = df.sort_index()
    f = ind.compute_all(df)
    f = add_structure(f)
    f["weekly_trend"] = weekly_trend_completed(df)
    f = add_patterns(f)
    return f.join(detect_geo(f))


def active_patterns(row: pd.Series) -> list:
    from .patterns_geo import GEO_LABELS

    return [label for col, label in {**PATTERN_LABELS, **GEO_LABELS}.items() if bool(row.get(col, False)) is True]
