"""Alert conditions evaluated on the latest two bars of a feature frame (bar t-1 → bar t).

Every condition is causal (uses bars ≤ t) and returns a human-readable message with the
values that triggered it. Evaluated after each ingest/scan and on a periodic tick.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

KINDS = {
    "price_above": "Price closes above a level",
    "price_below": "Price closes below a level",
    "price_cross": "Price crosses a level (either direction)",
    "entry_reached": "Price trades inside the setup's entry zone",
    "target_reached": "Price reaches a target",
    "stop_reached": "Price reaches the stop",
    "breakout": "Close above the prior 20-bar high",
    "breakdown": "Close below the prior 20-bar low",
    "volume_spike": "Volume above N× its 20-day average",
    "rsi_cross_above": "RSI crosses above a level",
    "rsi_cross_below": "RSI crosses below a level",
    "ema_cross_up": "Fast EMA crosses above slow EMA",
    "ema_cross_down": "Fast EMA crosses below slow EMA",
    "support_break": "Close below the last confirmed swing low",
    "resistance_break": "Close above the last confirmed swing high",
    "new_setup": "A new VALID setup matches a filter (market / strategy / direction / minimum score)",
    "unusual_options": "Unusual NIFTY options activity (open-interest change far above normal)",
}
PARAMS = {
    "price_above": ["level"], "price_below": ["level"], "price_cross": ["level"], "entry_reached": ["low", "high"],
    "target_reached": ["level", "direction"], "stop_reached": ["level", "direction"], "volume_spike": ["multiple"],
    "rsi_cross_above": ["level"], "rsi_cross_below": ["level"], "ema_cross_up": ["fast", "slow"], "ema_cross_down": ["fast", "slow"],
    "breakout": [], "breakdown": [], "support_break": [], "resistance_break": [],
    "new_setup": [], "unusual_options": [],
}
OPTIONAL_PARAMS = {"new_setup": ["market", "strategy", "direction", "min_score"], "unusual_options": ["multiple"]}
BAR_KINDS = set(KINDS) - {"new_setup", "unusual_options"}


def validate_params(kind: str, params: Dict) -> Dict:
    if kind not in KINDS:
        raise ValueError(f"Unknown alert kind {kind!r}")
    out = {}
    for k in PARAMS[kind]:
        if k not in params:
            raise ValueError(f"Alert {kind} needs parameter {k!r}")
        if k == "direction":
            if params[k] not in ("LONG", "SHORT"):
                raise ValueError("direction must be LONG or SHORT")
            out[k] = params[k]
        elif k in ("fast", "slow"):
            if int(params[k]) not in (20, 50, 100, 200):
                raise ValueError("EMA periods must be 20, 50, 100 or 200")
            out[k] = int(params[k])
        else:
            v = float(params[k])
            if not np.isfinite(v) or v <= 0:
                raise ValueError(f"{k} must be a positive number")
            out[k] = v
    if kind in ("ema_cross_up", "ema_cross_down") and out["fast"] >= out["slow"]:
        raise ValueError("fast EMA must be shorter than slow EMA")
    if kind == "entry_reached" and out["low"] > out["high"]:
        raise ValueError("low must be ≤ high")
    if kind == "new_setup":
        out = {k: params[k] for k in ("market", "strategy", "direction", "min_score") if params.get(k) not in (None, "")}
    if kind == "unusual_options":
        out = {"multiple": float(params.get("multiple", 3.0))}
    return out


def _fmt(x: float) -> str:
    from .levels import price_decimals

    return f"{x:.{price_decimals(abs(x))}f}"


def evaluate(kind: str, params: Dict, f: pd.DataFrame) -> Tuple[bool, Optional[str], Dict]:
    """f: feature frame (engine.features.build_features). Returns (triggered, message, values)."""
    if len(f) < 2:
        return False, None, {}
    r, p = f.iloc[-1], f.iloc[-2]
    c, pc, hi, lo = float(r.close), float(p.close), float(r.high), float(r.low)
    when = str(f.index[-1].date())
    v = {"close": c, "bar": when}
    if kind == "price_above":
        return c > params["level"] >= pc, f"closed at {_fmt(c)}, above {_fmt(params['level'])}", v
    if kind == "price_below":
        return c < params["level"] <= pc, f"closed at {_fmt(c)}, below {_fmt(params['level'])}", v
    if kind == "price_cross":
        L = params["level"]
        up, dn = pc <= L < c, pc >= L > c
        return up or dn, f"crossed {'above' if up else 'below'} {_fmt(L)} (close {_fmt(c)})", v
    if kind == "entry_reached":
        return lo <= params["high"] and hi >= params["low"], f"traded inside the entry zone {_fmt(params['low'])}–{_fmt(params['high'])}", v
    if kind in ("target_reached", "stop_reached"):
        L, long_ = params["level"], params["direction"] == "LONG"
        tgt = kind == "target_reached"
        hit = (hi >= L) if (long_ == tgt) else (lo <= L)
        return hit, f"{'target' if tgt else 'stop'} {_fmt(L)} reached (range {_fmt(lo)}–{_fmt(hi)})", v
    if kind == "breakout":
        lvl = float(r.get("hh20_prior", np.nan))
        return bool(np.isfinite(lvl) and c > lvl), f"closed at {_fmt(c)} above the prior 20-bar high {_fmt(lvl) if np.isfinite(lvl) else 'n/a'}", v
    if kind == "breakdown":
        lvl = float(r.get("ll20_prior", np.nan))
        return bool(np.isfinite(lvl) and c < lvl), f"closed at {_fmt(c)} below the prior 20-bar low {_fmt(lvl) if np.isfinite(lvl) else 'n/a'}", v
    if kind == "volume_spike":
        vr = float(r.get("vol_ratio20", np.nan))
        return bool(np.isfinite(vr) and vr >= params["multiple"]), f"volume {vr:.1f}× its 20-day average", {**v, "vol_ratio": vr}
    if kind in ("rsi_cross_above", "rsi_cross_below"):
        a, b, L = float(p.rsi), float(r.rsi), params["level"]
        hit = (a <= L < b) if kind == "rsi_cross_above" else (a >= L > b)
        return hit, f"RSI moved {a:.1f} → {b:.1f} across {L:g}", {**v, "rsi": b}
    if kind in ("ema_cross_up", "ema_cross_down"):
        fa, sa = f"ema{params['fast']}", f"ema{params['slow']}"
        d0, d1 = float(p[fa] - p[sa]), float(r[fa] - r[sa])
        hit = (d0 <= 0 < d1) if kind == "ema_cross_up" else (d0 >= 0 > d1)
        return hit, f"EMA{params['fast']} crossed {'above' if kind == 'ema_cross_up' else 'below'} EMA{params['slow']}", v
    if kind == "support_break":
        s = float(r.get("swing_low", np.nan))
        return bool(np.isfinite(s) and c < s <= pc), f"closed at {_fmt(c)} below support {_fmt(s) if np.isfinite(s) else 'n/a'}", v
    if kind == "resistance_break":
        s = float(r.get("swing_high", np.nan))
        return bool(np.isfinite(s) and c > s >= pc), f"closed at {_fmt(c)} above resistance {_fmt(s) if np.isfinite(s) else 'n/a'}", v
    return False, None, {}


def matches_setup(params: Dict, setup: Dict) -> bool:
    if params.get("market") and setup.get("market") != params["market"]:
        return False
    if params.get("strategy") and setup["strategy"]["id"] != params["strategy"]:
        return False
    if params.get("direction") and setup["direction"] != params["direction"]:
        return False
    if params.get("min_score") and setup["score"] < float(params["min_score"]):
        return False
    return True


def unusual_options(chain_rows: list, multiple: float = 3.0, top: int = 5) -> list:
    """Strikes whose |ΔOI| is ≥ `multiple` × the median |ΔOI| of the near expiry (context signal)."""
    changes = [abs(r.get(t, {}).get("oi_change") or 0) for r in chain_rows for t in ("CE", "PE") if r.get(t)]
    med = float(np.median(changes)) if changes else 0.0
    if med <= 0:
        return []
    out = [{"strike": r["strike"], "option_type": t, "oi_change": r[t]["oi_change"], "multiple_of_median": round(abs(r[t]["oi_change"]) / med, 1)}
           for r in chain_rows for t in ("CE", "PE") if r.get(t) and abs(r[t].get("oi_change") or 0) >= multiple * med]
    return sorted(out, key=lambda x: -x["multiple_of_median"])[:top]
