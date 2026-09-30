"""Signal-time feature matrix for historical events (no look-ahead: every value is read at the signal bar)."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# (column, how to orient for SHORT setups): "flip" = negate, "mirror100" = 100 - x, "mirror1" = 1 - x, None = direction-neutral
RAW = [
    ("rsi", "mirror100"), ("adx", None), ("atr_pct", None), ("atr_pct_rank", None), ("bb_width_rank", None), ("bb_pctb", "mirror1"),
    ("vol_ratio20", None), ("vol_ratio50", None), ("ret5", "flip"), ("ret20", "flip"), ("ret63", "flip"), ("ema200_slope", "flip"),
    ("close_loc", "mirror1"), ("weekly_trend", "flip"), ("supertrend_dir", "flip"), ("stoch_k", "mirror100"), ("mfi", "mirror100"), ("cci", "flip"),
]
DERIVED = ["dist_ema20_atr", "dist_ema50_atr", "dist_ema200_atr", "macd_hist_atr", "obv_slope_sign", "hh_hl", "rr_t2_planned", "score_at_signal", "breadth50"]
REGIMES = ["Strong Bull", "Weak Bull", "Range", "Bear", "Panic/Selloff", "Recovery"]


def feature_names(strategies: List[str]) -> List[str]:
    return [c for c, _ in RAW] + DERIVED + [f"regime={r}" for r in REGIMES] + [f"strategy={s}" for s in strategies]


def _orient(v: float, how: Optional[str], sign: int) -> float:
    if sign == 1 or how is None or not np.isfinite(v):
        return v
    return -v if how == "flip" else 100 - v if how == "mirror100" else 1 - v


def row_features(row: pd.Series, direction: str, regime: Optional[str], strategy_id: str, rr_t2: float, score: Optional[float],
                 breadth50: Optional[float], strategies: List[str]) -> Dict[str, float]:
    sign = 1 if direction == "LONG" else -1
    out = {c: _orient(float(row.get(c, np.nan)), how, sign) for c, how in RAW}
    atr = float(row.get("atr", np.nan)) or np.nan
    c = float(row["close"])
    for n in (20, 50, 200):
        out[f"dist_ema{n}_atr"] = sign * (c - float(row.get(f"ema{n}", np.nan))) / atr if atr and np.isfinite(atr) else np.nan
    out["macd_hist_atr"] = sign * float(row.get("macd_hist", np.nan)) / atr if atr and np.isfinite(atr) else np.nan
    out["obv_slope_sign"] = sign * float(np.sign(row.get("obv_slope", 0) or 0))
    hh = (bool(row.get("higher_high")) + bool(row.get("higher_low"))) if sign == 1 else (bool(row.get("lower_high")) + bool(row.get("lower_low")))
    out["hh_hl"] = float(hh)
    out["rr_t2_planned"] = float(rr_t2) if rr_t2 is not None else np.nan
    out["score_at_signal"] = float(score) if score is not None else np.nan
    out["breadth50"] = float(breadth50) if breadth50 is not None and np.isfinite(breadth50) else np.nan
    for r in REGIMES:
        out[f"regime={r}"] = 1.0 if regime == r else 0.0
    for s in strategies:
        out[f"strategy={s}"] = 1.0 if strategy_id == s else 0.0
    return out


def build_dataset(features: Dict[str, pd.DataFrame], events: pd.DataFrame, strategies: List[str],
                  breadth50: Optional[pd.Series] = None) -> pd.DataFrame:
    """One row per historical event: features at the signal bar + label (T1 before stop) + timing columns."""
    rows = []
    for e in events.itertuples(index=False):
        f = features.get(e.symbol)
        ts = pd.Timestamp(e.signal_date)
        if f is None or ts not in f.index:
            continue
        b = float(breadth50.get(ts, np.nan)) if breadth50 is not None else np.nan
        x = row_features(f.loc[ts], e.direction, getattr(e, "regime", None), e.strategy_id, getattr(e, "rr_t2_planned", np.nan),
                         getattr(e, "score_at_signal", None), b, strategies)
        x.update({"y": int(bool(e.t1_hit)), "signal_date": ts, "exit_date": pd.Timestamp(e.exit_date), "symbol": e.symbol,
                  "strategy_id": e.strategy_id, "regime_family": getattr(e, "regime_family", None), "r_multiple": e.r_multiple})
        rows.append(x)
    return pd.DataFrame(rows).sort_values("signal_date").reset_index(drop=True) if rows else pd.DataFrame()
