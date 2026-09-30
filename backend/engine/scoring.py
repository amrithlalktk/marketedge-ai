"""Multi-factor setup score (0–100). Each component is scored independently and
kept visible; the final score is a weighted average of the components that
have data. The score ranks setup quality; it is NOT a probability or guarantee."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import EngineConfig
from .regime import regime_score_for

FUNDAMENTAL_MODE_WEIGHTS = {"trend": 15, "momentum": 5, "volume": 5, "price_action": 5, "structure": 5,
                            "fundamental": 50, "volatility": 5, "regime": 5, "risk_reward": 5}


def _clip(x: float) -> float:
    return float(max(0.0, min(100.0, x)))


def _v(row: pd.Series, k: str, default=np.nan) -> float:
    try:
        x = float(row.get(k, default))
    except (TypeError, ValueError):
        return default
    return x


def score_trend(row: pd.Series, sign: int) -> float:
    c = _v(row, "close")
    pts = 0.0
    pts += 20 if sign * (c - _v(row, "ema20")) > 0 else 0
    pts += 20 if sign * (c - _v(row, "ema50")) > 0 else 0
    pts += 15 if sign * (c - _v(row, "ema200")) > 0 else 0
    pts += 15 if sign * (_v(row, "ema50") - _v(row, "ema200")) > 0 else 0
    pts += 10 if sign * _v(row, "ema200_slope", 0) > 0 else 0
    pts += 10 if _v(row, "supertrend_dir", 0) == sign else 0
    pts += 10 if _v(row, "weekly_trend", 0) == sign else (5 if _v(row, "weekly_trend", 0) == 0 else 0)
    return _clip(pts)


def score_momentum(row: pd.Series, sign: int) -> float:
    r = _v(row, "rsi", 50)
    rsi_dir = r if sign == 1 else 100 - r
    if 55 <= rsi_dir <= 70:
        rs = 40
    elif 50 <= rsi_dir < 55 or 70 < rsi_dir <= 75:
        rs = 28
    elif rsi_dir > 75:
        rs = 15  # stretched
    else:
        rs = 8
    m = 0
    m += 20 if sign * _v(row, "macd_hist", 0) > 0 else 0
    m += 15 if sign * _v(row, "macd", 0) > 0 else 0
    m += 15 if sign * _v(row, "roc", 0) > 0 else 0
    st = _v(row, "stoch_k", 50)
    m += 10 if (sign == 1 and 50 < st < 90) or (sign == -1 and 10 < st < 50) else 0
    return _clip(rs + m)


def score_volume(row: pd.Series, sign: int) -> Optional[float]:
    """None when the instrument has no meaningful volume (forex, indices)."""
    vs = _v(row, "vol_sma20", np.nan)
    if not np.isfinite(vs) or vs <= 0:
        return None
    vr = _v(row, "vol_ratio20", 1)
    base = 15 if vr < 1 else 35 if vr < 1.3 else 55 if vr < 1.5 else 70 if vr < 2 else 80
    base += 10 if sign * _v(row, "obv_slope", 0) > 0 else 0
    mfi = _v(row, "mfi", 50)
    base += 10 if (sign == 1 and 50 < mfi < 85) or (sign == -1 and 15 < mfi < 50) else 0
    return _clip(base)


def score_price_action(row: pd.Series, sign: int) -> float:
    bull = ["pat_breakout", "pat_bull_flag", "pat_double_bottom", "pat_support_bounce", "pat_trend_continuation", "pat_trend_reversal_up", "pat_consolidation"]
    bear = ["pat_breakdown", "pat_bear_flag", "pat_double_top", "pat_resistance_rejection", "pat_trend_continuation", "pat_trend_reversal_down", "pat_consolidation"]
    agree = sum(bool(row.get(k, False)) for k in (bull if sign == 1 else bear))
    against = sum(bool(row.get(k, False)) for k in (bear if sign == 1 else bull) if k not in ("pat_trend_continuation", "pat_consolidation"))
    cl = _v(row, "close_loc", 0.5)
    loc = cl if sign == 1 else 1 - cl
    return _clip(25 + 18 * agree - 20 * against + 30 * (loc - 0.5) * 2)


def score_structure(row: pd.Series, sign: int, room_r: Optional[float]) -> float:
    pts = 0.0
    if sign == 1:
        pts += 25 if bool(row.get("higher_high")) else 0
        pts += 25 if bool(row.get("higher_low")) else 0
    else:
        pts += 25 if bool(row.get("lower_high")) else 0
        pts += 25 if bool(row.get("lower_low")) else 0
    if room_r is None:
        pts += 50  # no opposing level in lookback (e.g. at highs)
    else:
        pts += 50 if room_r >= 3 else 35 if room_r >= 2 else 15 if room_r >= 1 else 0
    return _clip(pts)


def score_volatility(row: pd.Series) -> float:
    rk = _v(row, "atr_pct_rank", 0.5)
    if np.isnan(rk):
        return 50.0
    if 0.2 <= rk <= 0.75:
        return 90.0
    if rk < 0.2:
        return 70.0
    if rk <= 0.9:
        return 55.0
    return 20.0


def score_risk_reward(rr: float) -> float:
    return _clip(np.interp(rr, [1.0, 1.5, 2.0, 3.0, 4.0], [10, 40, 60, 85, 100]))


def score_fundamental(fund: Optional[dict], sign: int) -> Optional[float]:
    """Scores only fields that are present; returns None if nothing reliable exists."""
    if not fund:
        return None
    pts, maxpts = 0.0, 0.0
    def add(key, good, ok, weight=1.0, higher_better=True):
        nonlocal pts, maxpts
        v = fund.get(key)
        if v is None:
            return
        maxpts += weight
        if higher_better:
            pts += weight * (1.0 if v >= good else 0.5 if v >= ok else 0.0)
        else:
            pts += weight * (1.0 if v <= good else 0.5 if v <= ok else 0.0)
    add("revenue_growth_pct", 15, 5)
    add("profit_growth_pct", 15, 5)
    add("eps_growth_pct", 15, 5)
    add("roe_pct", 18, 12)
    add("roce_pct", 18, 12)
    add("debt_to_equity", 0.5, 1.0, higher_better=False)
    add("operating_margin_pct", 18, 10)
    add("peg", 1.2, 2.0, higher_better=False)
    add("promoter_holding_pct", 50, 35, weight=0.5)
    if maxpts == 0:
        return None
    s = 100 * pts / maxpts
    return _clip(s if sign == 1 else 100 - s)


def score_historical(prob: dict, min_sample: int = 30) -> Optional[float]:
    """Evidence strength of the same rules on history: expectancy after costs, T1 rate, and sample size."""
    n = prob.get("sample_size") or 0
    if n < min_sample or prob.get("expectancy_r") is None:
        return None
    s = 50 + 100 * float(np.clip(prob["expectancy_r"], -0.5, 0.5)) + 0.5 * ((prob.get("t1_hit_rate") or 40) - 40)
    confidence = min(1.0, n / 150)  # small samples are pulled toward neutral
    return round(_clip(50 + (s - 50) * confidence), 1)


def combine(comps: Dict[str, Optional[float]], weights: Dict[str, float]) -> float:
    used = {k: w for k, w in weights.items() if w > 0 and comps.get(k) is not None}
    total = sum(used.values()) or 1
    return round(sum(comps[k] * w for k, w in used.items()) / total, 1)


def compute_score(row: pd.Series, direction: str, rr_t2: float, room_r: Optional[float], regime: Optional[str],
                  vol_state: Optional[str], fundamentals: Optional[dict], cfg: EngineConfig) -> Tuple[float, Dict[str, Optional[float]], List[str]]:
    sign = 1 if direction == "LONG" else -1
    comps: Dict[str, Optional[float]] = {
        "trend": score_trend(row, sign),
        "momentum": score_momentum(row, sign),
        "volume": score_volume(row, sign),
        "price_action": score_price_action(row, sign),
        "structure": score_structure(row, sign, room_r),
        "fundamental": score_fundamental(fundamentals, sign),
        "volatility": score_volatility(row),
        "regime": regime_score_for(direction, regime, vol_state),
        "risk_reward": score_risk_reward(rr_t2),
        "historical": None,  # filled after the historical evidence is looked up
        "ml": None,          # filled when an active, validated ML model exists
    }
    weights = dict(cfg.weights)
    if cfg.analysis_mode == "technical":
        weights["fundamental"] = 0
    elif cfg.analysis_mode == "fundamental":
        weights = dict(FUNDAMENTAL_MODE_WEIGHTS)
    notes = []
    if comps["fundamental"] is None and weights.get("fundamental", 0) > 0:
        notes.append("Fundamental data unavailable — its weight was redistributed across technical components.")
    if comps["volume"] is None and weights.get("volume", 0) > 0:
        notes.append("No meaningful volume for this instrument (forex / index) — the volume weight was redistributed.")
    used = {k: w for k, w in weights.items() if w > 0 and comps.get(k) is not None}
    total_w = sum(used.values()) or 1
    score = sum(comps[k] * w for k, w in used.items()) / total_w
    return round(score, 1), {k: (round(v, 1) if v is not None else None) for k, v in comps.items()}, notes
