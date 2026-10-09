"""Built-in setup definitions.

Each strategy is a deterministic rule evaluated on the close of bar t using
only features known at t. Entry is always at the NEXT bar's open, so the rule
that finds today's setup is exactly the rule replayed over history by the
backtester and the target-hit probability engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd

from .config import LevelConfig


@dataclass
class StrategySpec:
    id: str
    name: str
    direction: str  # LONG | SHORT
    setup_type: str
    description: str
    conditions: List[str]
    detect: Callable[[pd.DataFrame], pd.Series] = field(repr=False)
    entry_rule: str = ("Next session open after the signal bar closes; skipped if the open gaps more than 0.5×ATR beyond the signal close "
                       "or leaves less than 0.5×ATR to the stop.")
    stop_rule: str = "Last confirmed swing low/high ± 0.2×ATR when it lies 1.5–3.5 ATR away; otherwise 2×ATR from entry."
    target_rule: str = "T1: first S/R level ≥1R away (capped at 2R, fallback 1.5R). T2: next level beyond T1 (fallback 3R). T3: 4.5R."
    max_hold_bars: int = 20
    timeframe: str = "1d"
    notes: str = ""
    levels: Optional[LevelConfig] = field(default=None, repr=False)  # strategy-specific exit geometry

    def public(self) -> dict:
        return {
            "id": self.id, "name": self.name, "direction": self.direction, "setup_type": self.setup_type,
            "description": self.description, "conditions": self.conditions, "entry_rule": self.entry_rule,
            "stop_rule": self.stop_rule, "target_rule": self.target_rule, "max_hold_bars": self.max_hold_bars,
            "timeframe": self.timeframe, "notes": self.notes,
        }


def _breakout_volume(f: pd.DataFrame) -> pd.Series:
    return (
        (f["close"] > f["hh20_prior"])
        & (f["vol_ratio20"] >= 1.5)
        & (f["close"] > f["ema50"])
        & (f["close_loc"] >= 0.6)
        & (f["adx"] >= 18)
        & (f["weekly_trend"] >= 0)
    )


def _pullback_ema20(f: pd.DataFrame) -> pd.Series:
    touched = (f["low"] <= f["ema20"] + 0.3 * f["atr"]).astype(float).rolling(3).max().fillna(0).astype(bool)
    return (
        (f["ema20"] > f["ema50"]) & (f["ema50"] > f["ema200"])
        & touched
        & (f["close"] > f["ema20"])
        & (f["close"] > f["high"].shift(1))
        & f["rsi"].between(45, 65)
        & (f["weekly_trend"] == 1)
    )


def _squeeze_breakout(f: pd.DataFrame) -> pd.Series:
    squeezed = (f["bb_width_rank"] <= 0.2).astype(float).rolling(5).max().shift(1).fillna(0).astype(bool)
    return squeezed & (f["close"] > f["bb_upper"]) & (f["vol_ratio20"] >= 1.3) & (f["close"] > f["ema200"])


def _support_bounce(f: pd.DataFrame) -> pd.Series:
    return (
        ((f["low"] - f["swing_low"]).abs() <= 0.5 * f["atr"])
        & (f["close"] > f["open"]) & (f["close_loc"] >= 0.6)
        & (f["close"] > f["ema200"])
        & (f["rsi"] < 50) & (f["rsi"] > f["rsi"].shift(1))
        & f["higher_low"]
    )


def _breakdown_volume(f: pd.DataFrame) -> pd.Series:
    return (
        (f["close"] < f["ll20_prior"])
        & (f["vol_ratio20"] >= 1.5)
        & (f["close"] < f["ema50"])
        & (f["close_loc"] <= 0.4)
        & (f["adx"] >= 18)
        & (f["weekly_trend"] <= 0)
    )


def _bear_pullback(f: pd.DataFrame) -> pd.Series:
    touched = (f["high"] >= f["ema20"] - 0.3 * f["atr"]).astype(float).rolling(3).max().fillna(0).astype(bool)
    return (
        (f["ema20"] < f["ema50"]) & (f["ema50"] < f["ema200"])
        & touched
        & (f["close"] < f["ema20"])
        & (f["close"] < f["low"].shift(1))
        & f["rsi"].between(35, 55)
        & (f["weekly_trend"] == -1)
    )


def _geo_breakout_up(f: pd.DataFrame) -> pd.Series:
    return f["pat_geo_breakout_up"] & (f["vol_ratio20"] >= 1.3) & (f["weekly_trend"] >= 0) & (f["close_loc"] >= 0.5)


def _geo_breakdown(f: pd.DataFrame) -> pd.Series:
    return f["pat_geo_breakout_down"] & (f["vol_ratio20"] >= 1.3) & (f["weekly_trend"] <= 0) & (f["close_loc"] <= 0.5)


def _cup_handle(f: pd.DataFrame) -> pd.Series:
    return f["pat_cup_handle_breakout"] & (f["vol_ratio20"] >= 1.3) & (f["close"] > f["ema200"])


SHORT_NOTE = "Overnight shorts in Indian cash equities are not permitted; SHORT setups require F&O (futures/options) or a market that allows shorting."

STRATEGIES: Dict[str, StrategySpec] = {
    s.id: s
    for s in [
        StrategySpec(
            "breakout_volume", "Breakout + Volume Confirmation", "LONG", "Breakout + Volume Confirmation",
            "Close above the prior 20-bar high on at least 1.5× average volume, in an established trend.",
            ["Close > prior 20-bar high", "Volume ≥ 1.5× 20-day average", "Close > 50 EMA", "Close in top 40% of bar range", "ADX ≥ 18", "Weekly trend not bearish (completed weeks)"],
            _breakout_volume,
        ),
        StrategySpec(
            "pullback_ema20", "Trend Pullback to 20 EMA", "LONG", "Trend Continuation (Pullback)",
            "Stacked EMAs with a pullback to the 20 EMA followed by a close above the prior high.",
            ["20 EMA > 50 EMA > 200 EMA", "Low within 0.3 ATR of 20 EMA in last 3 bars", "Close > 20 EMA and > prior high", "RSI 45–65", "Weekly trend bullish"],
            _pullback_ema20,
        ),
        StrategySpec(
            "squeeze_breakout", "Volatility Squeeze Breakout", "LONG", "Volatility Contraction Breakout",
            "Bollinger Band width in its lowest 20% (120 bars) followed by a close above the upper band on volume.",
            ["BB width percentile ≤ 20% within last 5 bars", "Close > upper Bollinger Band", "Volume ≥ 1.3× average", "Close > 200 EMA"],
            _squeeze_breakout,
        ),
        StrategySpec(
            "support_bounce", "Support Bounce (Higher Low)", "LONG", "Support Bounce",
            "Bullish reversal bar at the last confirmed swing low while structure prints a higher low above the 200 EMA.",
            ["Low within 0.5 ATR of last confirmed swing low", "Bullish close in top 40% of range", "Higher low structure", "RSI < 50 and rising", "Close > 200 EMA"],
            _support_bounce,
        ),
        StrategySpec(
            "breakdown_volume", "Breakdown + Volume Confirmation", "SHORT", "Breakdown + Volume Confirmation",
            "Close below the prior 20-bar low on at least 1.5× average volume in a weak trend.",
            ["Close < prior 20-bar low", "Volume ≥ 1.5× 20-day average", "Close < 50 EMA", "Close in bottom 40% of range", "ADX ≥ 18", "Weekly trend not bullish"],
            _breakdown_volume, notes=SHORT_NOTE,
        ),
        StrategySpec(
            "bear_pullback_ema20", "Downtrend Pullback to 20 EMA", "SHORT", "Trend Continuation (Bear Pullback)",
            "Stacked bearish EMAs with a rally into the 20 EMA followed by a close below the prior low.",
            ["20 EMA < 50 EMA < 200 EMA", "High within 0.3 ATR of 20 EMA in last 3 bars", "Close < 20 EMA and < prior low", "RSI 35–55", "Weekly trend bearish"],
            _bear_pullback, notes=SHORT_NOTE,
        ),
        StrategySpec(
            "geo_breakout", "Triangle / Rectangle Breakout", "LONG", "Pattern Breakout",
            "Close breaks above the upper boundary of a rectangle or triangle drawn from confirmed pivots, on volume.",
            ["Rectangle or triangle present on the prior bar (≥2 confirmed highs and lows on the lines)", "Close > upper line + 0.1 ATR (prior close inside)",
             "Volume ≥ 1.3× average", "Close in upper half of range", "Weekly trend not bearish"],
            _geo_breakout_up,
        ),
        StrategySpec(
            "geo_breakdown", "Triangle / Rectangle Breakdown", "SHORT", "Pattern Breakdown",
            "Close breaks below the lower boundary of a rectangle or triangle drawn from confirmed pivots, on volume.",
            ["Rectangle or triangle present on the prior bar", "Close < lower line − 0.1 ATR (prior close inside)", "Volume ≥ 1.3× average",
             "Close in lower half of range", "Weekly trend not bullish"],
            _geo_breakdown, notes=SHORT_NOTE,
        ),
        StrategySpec(
            "cup_handle", "Cup-and-Handle Breakout", "LONG", "Cup and Handle",
            "Close above the rim of a rounded 12–35 % deep cup (30–150 bars) after a shallow handle, on volume, above the 200 EMA.",
            ["Two confirmed rims within 5 %", "Rounded (U-shaped) bottom, depth 12–35 %", "Handle ≤ ⅓ of cup depth, 3–25 bars",
             "Close crosses above the rim", "Volume ≥ 1.3× average", "Close > 200 EMA"],
            _cup_handle,
        ),
    ]
}


def detect(spec: StrategySpec, features: pd.DataFrame) -> pd.Series:
    return spec.detect(features).fillna(False).astype(bool)


# ---------------------------------------------------------------------------
# Index setups (NIFTY and other indices). Index "volume" is not a traded quantity,
# so these rules use price structure only. Holding is short (≤5 sessions) because
# they drive option setups whose expected holding period is 1–3 days.
# ---------------------------------------------------------------------------
def _idx_breakout(f: pd.DataFrame) -> pd.Series:
    return ((f["close"] > f["hh20_prior"]) & (f["close"] > f["ema50"]) & (f["adx"] >= 18)
            & (f["close_loc"] >= 0.6) & (f["weekly_trend"] >= 0))


def _idx_breakdown(f: pd.DataFrame) -> pd.Series:
    return ((f["close"] < f["ll20_prior"]) & (f["close"] < f["ema50"]) & (f["adx"] >= 18)
            & (f["close_loc"] <= 0.4) & (f["weekly_trend"] <= 0))


def _idx_squeeze_up(f: pd.DataFrame) -> pd.Series:
    squeezed = (f["bb_width_rank"] <= 0.2).astype(float).rolling(5).max().shift(1).fillna(0).astype(bool)
    return squeezed & (f["close"] > f["bb_upper"]) & (f["close"] > f["ema50"])


def _idx_squeeze_down(f: pd.DataFrame) -> pd.Series:
    squeezed = (f["bb_width_rank"] <= 0.2).astype(float).rolling(5).max().shift(1).fillna(0).astype(bool)
    return squeezed & (f["close"] < f["bb_lower"]) & (f["close"] < f["ema50"])


_IDX_HOLD = 5
INDEX_STRATEGIES: Dict[str, StrategySpec] = {
    s.id: s
    for s in [
        StrategySpec("idx_breakout", "Index Breakout", "LONG", "Breakout",
                     "Index closes above its prior 20-session high in an established trend.",
                     ["Close > prior 20-session high", "Close > 50 EMA", "ADX ≥ 18", "Close in top 40% of range", "Weekly trend not bearish"],
                     _idx_breakout, max_hold_bars=_IDX_HOLD),
        StrategySpec("idx_pullback", "Index Trend Pullback", "LONG", "Trend Continuation (Pullback)",
                     "Stacked EMAs; pullback to the 20 EMA resolves with a close above the prior high.",
                     ["20 EMA > 50 EMA > 200 EMA", "Low within 0.3 ATR of 20 EMA (3 bars)", "Close > prior high", "RSI 45–65", "Weekly trend bullish"],
                     _pullback_ema20, max_hold_bars=_IDX_HOLD),
        StrategySpec("idx_squeeze_up", "Index Volatility Squeeze Breakout", "LONG", "Volatility Contraction Breakout",
                     "Bollinger width in lowest 20% then a close above the upper band.",
                     ["BB width percentile ≤ 20% (last 5 bars)", "Close > upper band", "Close > 50 EMA"],
                     _idx_squeeze_up, max_hold_bars=_IDX_HOLD),
        StrategySpec("idx_breakdown", "Index Breakdown", "SHORT", "Breakdown",
                     "Index closes below its prior 20-session low in a weak trend.",
                     ["Close < prior 20-session low", "Close < 50 EMA", "ADX ≥ 18", "Close in bottom 40% of range", "Weekly trend not bullish"],
                     _idx_breakdown, max_hold_bars=_IDX_HOLD),
        StrategySpec("idx_bear_pullback", "Index Downtrend Pullback", "SHORT", "Trend Continuation (Bear Pullback)",
                     "Stacked bearish EMAs; rally into the 20 EMA resolves with a close below the prior low.",
                     ["20 EMA < 50 EMA < 200 EMA", "High within 0.3 ATR of 20 EMA (3 bars)", "Close < prior low", "RSI 35–55", "Weekly trend bearish"],
                     _bear_pullback, max_hold_bars=_IDX_HOLD),
        StrategySpec("idx_squeeze_down", "Index Volatility Squeeze Breakdown", "SHORT", "Volatility Contraction Breakdown",
                     "Bollinger width in lowest 20% then a close below the lower band.",
                     ["BB width percentile ≤ 20% (last 5 bars)", "Close < lower band", "Close < 50 EMA"],
                     _idx_squeeze_down, max_hold_bars=_IDX_HOLD),
    ]
}
