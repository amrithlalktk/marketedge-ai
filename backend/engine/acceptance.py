"""Strategy acceptance: is a strategy validated out of sample, as of a date?

Evaluated on every scan from the historical event set, using only trades that EXITED before `as_of`.
The available history is split in time: the first part (design) and the most recent part (out-of-sample,
`oos_fraction` of the span). Built-in rules are fixed (no parameters are fitted), so both parts are honest
history; requiring both — and the recent part with a margin for noise — rejects edges that faded.

A strategy that fails is UNVALIDATED: its setups are never published as trade ideas (status NO_TRADE with
the reason) but may be tracked as paper trades so forward evidence accumulates.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .config import ValidationConfig
from .diagnostics import max_drawdown_r, outcome_stats

VALIDATED, UNVALIDATED = "VALIDATED", "UNVALIDATED"


def _years_positive(df: pd.DataFrame) -> Optional[float]:
    years = df.groupby(pd.to_datetime(df["signal_date"]).dt.year)["r_multiple"].agg(["mean", "size"])
    years = years[years["size"] >= 20]
    return float((years["mean"] > 0).mean()) if len(years) else None


def evaluate(events: pd.DataFrame, strategy_ids: Iterable[str], as_of, cfg: ValidationConfig) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    as_of = pd.Timestamp(as_of)
    e = events
    if e is not None and not e.empty:
        e = e[pd.to_datetime(e["exit_date"]) < as_of]
    for sid in strategy_ids:
        g = e[e["strategy_id"] == sid] if e is not None and not e.empty else pd.DataFrame()
        if g.empty:
            out[sid] = {"status": UNVALIDATED, "reasons": ["No historical trades"], "trades": 0}
            continue
        sig = pd.to_datetime(g["signal_date"])
        lo = sig.min()
        split = lo + (as_of - lo) * (1 - cfg.accept_oos_fraction)
        design, oos = g[sig < split], g[sig >= split]
        d, o = outcome_stats(design), outcome_stats(oos)
        reasons: List[str] = []
        n = o.get("trades", 0)
        if n < cfg.accept_min_trades:
            reasons.append(f"only {n} out-of-sample trades (min {cfg.accept_min_trades})")
        if n:
            lcb = o["expectancy_r"] - cfg.accept_lcb_z * (o.get("expectancy_se") or 0)
            if lcb <= 0:
                reasons.append(f"out-of-sample expectancy {o['expectancy_r']:+.3f} R (± {o.get('expectancy_se') or 0:.3f}) is not above zero "
                               f"with a {cfg.accept_lcb_z:g}-SE margin")
            if (o.get("profit_factor") or 0) < cfg.accept_min_profit_factor:
                reasons.append(f"out-of-sample profit factor {o.get('profit_factor')} (min {cfg.accept_min_profit_factor:g})")
            dd = max_drawdown_r(oos.assign(exit_ts=pd.to_datetime(oos["exit_date"])))
            if dd is not None and dd > cfg.accept_max_drawdown_r:
                reasons.append(f"out-of-sample max drawdown {dd:g} R (max {cfg.accept_max_drawdown_r:g} R)")
        if cfg.accept_require_design_positive and (d.get("expectancy_r") is None or d["expectancy_r"] <= 0):
            reasons.append(f"design-period expectancy {d.get('expectancy_r')} R is not positive")
        yp = _years_positive(g)
        if yp is not None and yp < cfg.accept_min_positive_years:
            reasons.append(f"positive in only {round(100 * yp)}% of years (min {round(100 * cfg.accept_min_positive_years)}%)")
        out[sid] = {
            "status": VALIDATED if not reasons else UNVALIDATED, "reasons": reasons,
            "split": str(split.date()), "design_period": [str(lo.date()), str((split - pd.Timedelta(days=1)).date())],
            "oos_period": [str(split.date()), str(as_of.date())],
            "design": {k: d.get(k) for k in ("trades", "t1_rate", "stop_rate", "expectancy_r", "profit_factor")},
            "out_of_sample": {k: o.get(k) for k in ("trades", "t1_rate", "t2_rate", "stop_rate", "expectancy_r", "expectancy_se",
                                                    "profit_factor", "max_drawdown_r", "avg_bars_held", "win_rate")},
            "years_positive_pct": None if yp is None else round(100 * yp),
        }
    return out


def calibration_status(events: pd.DataFrame, as_of, cfg: ValidationConfig) -> dict:
    """How the 'chance of Target 1' the app showed compared with what happened, over the out-of-sample part,
    for the trades the evidence gate would have shown (same conditioning as probability.estimate)."""
    from .diagnostics import GatePolicy, calibration, gate_replay

    if events is None or events.empty:
        return {"status": "no data"}
    e = events[pd.to_datetime(events["exit_date"]) < pd.Timestamp(as_of)]
    if e.empty:
        return {"status": "no data"}
    g = gate_replay(e, GatePolicy("calibration", min_sample=cfg.min_sample_size, use_score=False, min_rr=0, max_stop_pct=1e9, min_t1_r=0))
    lo = g["signal_ts"].min()
    split = lo + (pd.Timestamp(as_of) - lo) * (1 - cfg.accept_oos_fraction)
    shown = g[(g["signal_ts"] >= split) & g["evidence_n"].gt(0)]
    out = calibration(shown)
    out["period"] = [str(split.date()), str(pd.Timestamp(as_of).date())]
    return out


def summary_line(res: dict) -> str:
    o = res.get("out_of_sample") or {}
    if res["status"] == VALIDATED:
        return (f"VALIDATED out of sample ({res['oos_period'][0]} → {res['oos_period'][1]}): {o.get('trades')} trades, "
                f"expectancy {o.get('expectancy_r'):+.3f} R, profit factor {o.get('profit_factor')}")
    return "UNVALIDATED — paper trade only: " + "; ".join(res.get("reasons") or ["insufficient evidence"])


def as_check_detail(res: Optional[dict]) -> str:
    return summary_line(res) if res else "UNVALIDATED — no out-of-sample evaluation available"


def finite(x) -> Optional[float]:
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else x
