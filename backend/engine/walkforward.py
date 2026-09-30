"""Train / validation / out-of-sample splits, walk-forward evaluation and
robustness checks.

Built-in strategies have fixed rules, so "training" here means choosing
parameters (from an explicit grid) using ONLY the training window, then
measuring the chosen parameters on the following unseen window.
"""
from __future__ import annotations

import itertools
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .metrics import hit_rates


def segment_stats(trades: List[dict], segments: Dict[str, Tuple[str, str]]) -> Dict[str, dict]:
    out = {}
    for name, (a, b) in segments.items():
        sub = [t for t in trades if a <= t["signal_date"] <= b and t["exit_date"] <= b]
        out[name] = {"period": [a, b], **hit_rates(sub)}
    return out


def default_segments(start: str, end: str) -> Dict[str, Tuple[str, str]]:
    """~60% training, ~20% validation, ~20% out-of-sample, contiguous and ordered."""
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    span = e - s
    t1, t2 = s + span * 0.6, s + span * 0.8
    d = lambda x: str(x.date())
    return {"training": (d(s), d(t1)), "validation": (d(t1 + pd.Timedelta(days=1)), d(t2)), "out_of_sample": (d(t2 + pd.Timedelta(days=1)), d(e))}


def walk_forward_windows(start: str, end: str, train_years: float = 3, test_years: float = 1) -> List[Dict[str, str]]:
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    windows = []
    cur = s
    while True:
        tr_end = cur + pd.DateOffset(months=int(train_years * 12)) - pd.Timedelta(days=1)
        te_end = tr_end + pd.DateOffset(months=int(test_years * 12))
        if tr_end >= e:
            break
        windows.append({"train": [str(cur.date()), str(tr_end.date())], "test": [str((tr_end + pd.Timedelta(days=1)).date()), str(min(te_end, e).date())]})
        if te_end >= e:
            break
        cur = cur + pd.DateOffset(months=int(test_years * 12))
    return windows


def walk_forward(run: Callable[[dict, str, str], List[dict]], grid: Dict[str, list], start: str, end: str,
                 train_years: float = 3, test_years: float = 1, min_trades: int = 20) -> Dict:
    """`run(params, start, end)` must return trades for that parameter set and window."""
    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())] if grid else [{}]
    folds, oos_trades = [], []
    for w in walk_forward_windows(start, end, train_years, test_years):
        best, best_exp, is_stats = None, -np.inf, None
        for p in combos:
            st = hit_rates(run(p, *w["train"]))
            if st.get("sample_size", 0) >= min_trades and st["expectancy_r"] > best_exp:
                best, best_exp, is_stats = p, st["expectancy_r"], st
        if best is None:
            folds.append({**w, "params": None, "note": "No parameter set met the minimum trade count in training"})
            continue
        test_trades = [t for t in run(best, *w["test"]) if t["exit_date"] <= w["test"][1]]
        oos_trades.extend(test_trades)
        folds.append({**w, "params": best, "in_sample": is_stats, "out_of_sample": hit_rates(test_trades)})
    oos = hit_rates(oos_trades)
    return {"folds": folds, "out_of_sample_combined": oos, "oos_trades": oos_trades}


def parameter_sensitivity(run: Callable[[dict, str, str], List[dict]], grid: Dict[str, list], start: str, end: str) -> Dict:
    combos = [dict(zip(grid, v)) for v in itertools.product(*grid.values())]
    rows = []
    for p in combos:
        st = hit_rates(run(p, start, end))
        rows.append({"params": p, "trades": st.get("sample_size", 0), "expectancy_r": st.get("expectancy_r"), "t1_hit_rate": st.get("t1_hit_rate")})
    exps = [r["expectancy_r"] for r in rows if r["expectancy_r"] is not None]
    spread = float(np.std(exps)) if len(exps) > 1 else 0.0
    return {"results": rows, "expectancy_std": round(spread, 3), "positive_share": round(100 * float(np.mean([e > 0 for e in exps])), 1) if exps else None}


def overfit_warnings(segments: Dict[str, dict], wf: Optional[Dict] = None, sensitivity: Optional[Dict] = None) -> List[str]:
    w = []
    tr, oos = segments.get("training", {}), segments.get("out_of_sample", {})
    if tr.get("sample_size", 0) and oos.get("sample_size", 0):
        if tr["expectancy_r"] > 0 and oos["expectancy_r"] <= 0:
            w.append("Out-of-sample expectancy is not positive although training was — likely overfit or regime-dependent.")
        elif tr["expectancy_r"] > 0 and oos["expectancy_r"] < 0.5 * tr["expectancy_r"]:
            w.append("Out-of-sample expectancy is less than half of training expectancy — performance degrades on unseen data.")
    if oos.get("sample_size", 0) < 30:
        w.append(f"Out-of-sample period has only {oos.get('sample_size', 0)} trades — too few to confirm robustness.")
    if wf:
        o = wf.get("out_of_sample_combined", {})
        if o.get("sample_size", 0) and o.get("expectancy_r", 0) <= 0:
            w.append("Walk-forward out-of-sample expectancy is not positive.")
    if sensitivity and sensitivity.get("positive_share") is not None and sensitivity["positive_share"] < 60:
        w.append("Fewer than 60% of neighbouring parameter sets are profitable — results are parameter-sensitive.")
    return w


def yearly_stability(trades: List[dict]) -> Dict:
    """Per-calendar-year hit rates/expectancy: does the edge persist across different periods?"""
    by = {}
    for t in trades:
        by.setdefault(t["signal_date"][:4], []).append(t)
    rows = [{"year": y, **hit_rates(ts)} for y, ts in sorted(by.items())]
    exps = [r["expectancy_r"] for r in rows if r.get("sample_size", 0) >= 10]
    return {"years": rows, "positive_years": sum(e > 0 for e in exps), "years_evaluated": len(exps)}


def too_good_warnings(summary: Dict, stability: Optional[Dict] = None) -> List[str]:
    w = []
    n = summary.get("sample_size", 0)
    pf = summary.get("profit_factor")
    if n and n < 100 and pf and pf > 2.5:
        w.append(f"Profit factor {pf} on only {n} trades is unusually high — likely luck or curve-fitting; demand more out-of-sample evidence.")
    if summary.get("win_rate") and summary["win_rate"] > 80 and n < 200:
        w.append("Win rate above 80% on a small sample is rarely sustainable.")
    if stability and stability["years_evaluated"] >= 3 and stability["positive_years"] / stability["years_evaluated"] < 0.5:
        w.append(f"Positive expectancy in only {stability['positive_years']} of {stability['years_evaluated']} years — the edge is period-dependent.")
    return w
