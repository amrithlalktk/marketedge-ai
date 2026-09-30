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
    entry_rule: str = "Next session open after the signal bar closes; skipped if the open gaps more than 0.5×ATR beyond the signal close."
    stop_rule: str = "Last confirmed swing low/high ± 0.2×ATR when it lies 0.8–3.5 ATR away; otherwise 2×ATR from entry."
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
# No-code strategy builder: a whitelisted, eval-free rule DSL (v2, v1-compatible).
#
#   {"name": "...", "direction": "LONG",
#    "conditions": [ <cond>, ... ],            # all must hold (AND)
#    "any_of":     [[<cond>, ...], [...]],      # optional: at least one group must fully hold (OR of ANDs)
#    "exits": {"stop_method": "structure"|"atr", "atr_stop_mult": 2.0,
#              "t1_r": 1.5, "t2_r": 3.0, "max_hold_bars": 20}}
#
#   <cond>    = {"left": <operand>, "op": ">"|">="|"<"|"<="|"crosses_above"|"crosses_below", "right": <operand>}
#   <operand> = number | "feature" | {"feature": "vol_sma20", "mult": 1.5, "shift": 0}
#               mult scales the feature; shift=n reads the value n bars ago (never forward).
# Boolean features (patterns, structure flags) evaluate as 1.0 / 0.0.
# ---------------------------------------------------------------------------
ALLOWED_FEATURES = {
    "open", "high", "low", "close", "volume", "ema20", "ema50", "ema100", "ema200", "sma20", "sma50", "sma200",
    "rsi", "macd", "macd_signal", "macd_hist", "adx", "plus_di", "minus_di", "atr", "atr_pct", "atr_pct_rank",
    "bb_upper", "bb_lower", "bb_mid", "bb_width", "bb_width_rank", "bb_pctb", "stoch_k", "stoch_d", "cci", "roc", "obv",
    "obv_slope", "mfi", "vwap20", "supertrend", "supertrend_dir", "vol_sma20", "vol_sma50", "vol_ratio20", "vol_ratio50",
    "hh20_prior", "ll20_prior", "high_52w", "low_52w", "ret1", "ret5", "ret20", "ret63", "ema200_slope", "close_loc",
    "weekly_trend", "swing_high", "swing_low", "prev_swing_high", "prev_swing_low",
    "higher_high", "higher_low", "lower_high", "lower_low",
    "pat_breakout", "pat_breakdown", "pat_consolidation", "pat_gap_up", "pat_gap_down", "pat_double_bottom", "pat_double_top",
    "pat_bull_flag", "pat_bear_flag", "pat_support_bounce", "pat_resistance_rejection", "pat_trend_continuation",
    "pat_trend_reversal_up", "pat_trend_reversal_down",
}
_OPS = {
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    "crosses_above": None, "crosses_below": None,
}
_EXIT_LIMITS = {"atr_stop_mult": (0.5, 6.0), "t1_r": (0.5, 5.0), "t2_r": (1.0, 10.0), "max_hold_bars": (1, 120)}
MAX_CONDITIONS = 12
MAX_GROUPS = 4


class StrategyDefinitionError(ValueError):
    pass


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_operand(v) -> None:
    if _is_num(v):
        return
    if isinstance(v, str):
        if v not in ALLOWED_FEATURES:
            raise StrategyDefinitionError(f"Unknown operand: {v!r}")
        return
    if isinstance(v, dict):
        if set(v) - {"feature", "mult", "shift"} or v.get("feature") not in ALLOWED_FEATURES:
            raise StrategyDefinitionError(f"Unknown operand: {v!r}")
        if "mult" in v and not (_is_num(v["mult"]) and 0.01 <= v["mult"] <= 100):
            raise StrategyDefinitionError("mult must be a number between 0.01 and 100")
        if "shift" in v and not (isinstance(v["shift"], int) and not isinstance(v["shift"], bool) and 0 <= v["shift"] <= 20):
            raise StrategyDefinitionError("shift must be an integer 0–20 (bars ago; the future is never accessible)")
        return
    raise StrategyDefinitionError(f"Unknown operand: {v!r}")


def _operand(f: pd.DataFrame, x):
    if _is_num(x):
        return float(x)
    if isinstance(x, str):
        return f[x].astype(float)
    s = f[x["feature"]].astype(float)
    if x.get("shift"):
        s = s.shift(int(x["shift"]))
    return s * float(x.get("mult", 1.0))


def operand_text(x) -> str:
    if _is_num(x):
        return f"{x:g}"
    if isinstance(x, str):
        return x
    t = x["feature"] + (f"[{x['shift']}]" if x.get("shift") else "")
    return (f"{x['mult']:g}×" if x.get("mult", 1) != 1 else "") + t


def condition_text(c: dict) -> str:
    return f"{operand_text(c['left'])} {c['op'].replace('_', ' ')} {operand_text(c['right'])}"


def _check_condition(c: dict) -> None:
    if not isinstance(c, dict) or c.get("op") not in _OPS:
        raise StrategyDefinitionError(f"Unsupported operator {c.get('op') if isinstance(c, dict) else c!r}")
    _check_operand(c.get("left"))
    _check_operand(c.get("right"))
    if _is_num(c["left"]) and _is_num(c["right"]):
        raise StrategyDefinitionError("A condition must reference at least one feature")


def validate_definition(defn: dict) -> None:
    if defn.get("direction") not in ("LONG", "SHORT"):
        raise StrategyDefinitionError("direction must be LONG or SHORT")
    conds = defn.get("conditions") or []
    groups = defn.get("any_of") or []
    if len(groups) > MAX_GROUPS:
        raise StrategyDefinitionError(f"At most {MAX_GROUPS} OR-groups")
    total = len(conds) + sum(len(g) for g in groups)
    if not 1 <= total <= MAX_CONDITIONS:
        raise StrategyDefinitionError(f"between 1 and {MAX_CONDITIONS} conditions are required in total")
    for g in groups:
        if not isinstance(g, list) or not g:
            raise StrategyDefinitionError("each any_of group must be a non-empty list of conditions")
    for c in list(conds) + [c for g in groups for c in g]:
        _check_condition(c)
    ex = defn.get("exits") or {}
    if set(ex) - set(_EXIT_LIMITS) - {"stop_method"}:
        raise StrategyDefinitionError(f"Unknown exit parameter(s): {sorted(set(ex) - set(_EXIT_LIMITS) - {'stop_method'})}")
    if ex.get("stop_method", "structure") not in ("structure", "atr"):
        raise StrategyDefinitionError("stop_method must be 'structure' or 'atr'")
    for k, (lo, hi) in _EXIT_LIMITS.items():
        if k in ex and not (_is_num(ex[k]) and lo <= ex[k] <= hi):
            raise StrategyDefinitionError(f"{k} must be between {lo} and {hi}")
    if "t1_r" in ex and "t2_r" in ex and ex["t2_r"] <= ex["t1_r"]:
        raise StrategyDefinitionError("t2_r must be greater than t1_r")


def _eval(f: pd.DataFrame, c: dict) -> pd.Series:
    a, b = _operand(f, c["left"]), _operand(f, c["right"])
    if c["op"] in ("crosses_above", "crosses_below"):
        ap = a.shift(1) if isinstance(a, pd.Series) else a
        bp = b.shift(1) if isinstance(b, pd.Series) else b
        m = (a > b) & (ap <= bp) if c["op"] == "crosses_above" else (a < b) & (ap >= bp)
    else:
        m = _OPS[c["op"]](a, b)
    return m.fillna(False).astype(bool) if isinstance(m, pd.Series) else pd.Series(bool(m), index=f.index)


def exit_levels(defn: dict) -> Optional[LevelConfig]:
    ex = defn.get("exits") or {}
    if not ex:
        return None
    lv = LevelConfig()
    if "atr_stop_mult" in ex:
        lv.atr_stop_mult = float(ex["atr_stop_mult"])
        lv.max_stop_atr = max(lv.max_stop_atr, lv.atr_stop_mult)
    if ex.get("stop_method") == "atr":
        lv.swing_lookback = 0  # no structure stop: always the ATR stop
    if "t1_r" in ex:
        lv.t1_r = float(ex["t1_r"])
    if "t2_r" in ex:
        lv.t2_r = float(ex["t2_r"])
        lv.t3_r = max(lv.t3_r, lv.t2_r + 1.5)
    return lv


def build_custom_strategy(defn: dict) -> StrategySpec:
    validate_definition(defn)
    conds = list(defn.get("conditions") or [])
    groups = [list(g) for g in (defn.get("any_of") or [])]

    def detect(f: pd.DataFrame) -> pd.Series:
        mask = pd.Series(True, index=f.index)
        for c in conds:
            mask &= _eval(f, c)
        if groups:
            anyg = pd.Series(False, index=f.index)
            for g in groups:
                gm = pd.Series(True, index=f.index)
                for c in g:
                    gm &= _eval(f, c)
                anyg |= gm
            mask &= anyg
        return mask

    text = [condition_text(c) for c in conds]
    if groups:
        text.append("ANY OF: " + " | ".join("(" + " AND ".join(condition_text(c) for c in g) + ")" for g in groups))
    ex = defn.get("exits") or {}
    hold = int(ex.get("max_hold_bars", defn.get("max_hold_bars", 20)))
    lv = exit_levels(defn)
    stop_rule = ("ATR stop: " if ex.get("stop_method") == "atr" else "Structure stop with ATR fallback: ") + f"{(lv or LevelConfig()).atr_stop_mult:g}×ATR"
    target_rule = f"T1 {(lv or LevelConfig()).t1_r:g}R / T2 {(lv or LevelConfig()).t2_r:g}R (S/R-aware)"
    sid = str(defn.get("id") or "custom")
    return StrategySpec(sid, str(defn.get("name") or "Custom strategy"), defn["direction"], "Custom",
                        "User-defined rule set: " + " AND ".join(text), text, detect, max_hold_bars=hold,
                        stop_rule=stop_rule, target_rule=target_rule, levels=lv)


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


# ---------------------------------------------------------------------------
# Price-only swing setups for markets without consolidated volume (forex).
# Same detectors as the index set, standard swing holding (≤20 sessions) and
# the standard swing level geometry.
# ---------------------------------------------------------------------------
PRICE_ONLY_STRATEGIES: Dict[str, StrategySpec] = {}
for _s in INDEX_STRATEGIES.values():
    _id = _s.id.replace("idx_", "px_")
    PRICE_ONLY_STRATEGIES[_id] = StrategySpec(
        _id, _s.name.replace("Index", "Price-only"), _s.direction, _s.setup_type, _s.description.replace("Index", "Price"),
        _s.conditions, _s.detect, max_hold_bars=20, timeframe="1d",
        notes="Price structure only — no volume confirmation (forex has no consolidated volume).")


def strategy_set(name: str) -> Dict[str, StrategySpec]:
    return PRICE_ONLY_STRATEGIES if name == "price_only" else STRATEGIES
