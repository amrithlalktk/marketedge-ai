"""Target-hit probability engine.

For a live setup we look up historical trades produced by the SAME strategy,
entry, stop and target rules (from `backtest.backtest_symbol`) and report
their outcomes. Similarity conditioning is hierarchical and always disclosed:

    1. same strategy + same regime family + same score bucket
    2. same strategy + same regime family
    3. same strategy (all regimes)

The first level with at least `min_sample` trades is used. Only trades whose
EXIT date is strictly before the setup's as-of date are eligible, so the
estimate never uses information from the future.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .metrics import hit_rates


def score_bucket(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    return "75+" if score >= 75 else "60-75" if score >= 60 else "<60"


def estimate(events: pd.DataFrame, strategy_id: str, as_of: str, regime_family: Optional[str],
             score_bkt: Optional[str], min_sample: int, market: str, timeframe: str) -> Dict:
    if events is None or events.empty:
        return {"available": False, "sample_size": 0, "reason": "No historical trades available"}
    e = events[(events["strategy_id"] == strategy_id) & (pd.to_datetime(events["exit_date"]) < pd.Timestamp(as_of))]
    levels = []
    if regime_family and score_bkt:
        levels.append(("strategy + regime + score bucket", (e["regime_family"] == regime_family) & (e["score_bucket"] == score_bkt)))
    if regime_family:
        levels.append(("strategy + regime", e["regime_family"] == regime_family))
    levels.append(("strategy (all regimes)", pd.Series(True, index=e.index)))
    chosen_label, chosen = levels[-1][0], e
    for label, mask in levels:
        sub = e[mask]
        if len(sub) >= min_sample:
            chosen_label, chosen = label, sub
            break
    stats = hit_rates(chosen.to_dict("records"))
    n = stats.get("sample_size", 0)
    return {
        "available": n > 0,
        "sufficient": n >= min_sample,
        "min_sample": min_sample,
        "conditioning": chosen_label,
        "regime_family": regime_family,
        "score_bucket": score_bkt,
        "market": market,
        "timeframe": timeframe,
        "backtest_period": [str(pd.to_datetime(chosen["signal_date"]).min().date()), str(pd.to_datetime(chosen["exit_date"]).max().date())] if n else None,
        "symbols_covered": int(chosen["symbol"].nunique()) if n else 0,
        "definitions": {
            "t1_hit_rate": "Share of historical trades where Target 1 was touched before the initial stop.",
            "t2_hit_rate": "Share where Target 2 was touched before the initial stop.",
            "stop_rate": "Share where the initial stop was hit before Target 1.",
            "neither_rate": "Share that timed out without reaching Target 1 or the stop.",
            "same_bar_rule": "If one bar touches both stop and target, the stop is assumed first.",
        },
        **stats,
    }


def similar_examples(events: pd.DataFrame, strategy_id: str, as_of: str, symbol: Optional[str] = None, limit: int = 10) -> List[dict]:
    if events is None or events.empty:
        return []
    e = events[(events["strategy_id"] == strategy_id) & (pd.to_datetime(events["exit_date"]) < pd.Timestamp(as_of))]
    if symbol:
        own = e[e["symbol"] == symbol]
        if len(own):
            e = own
    cols = ["symbol", "signal_date", "entry", "stop", "t1", "t2", "exit_date", "exit_reason", "t1_hit", "t2_hit", "stop_hit", "net_return_pct", "bars_held", "regime"]
    cols = [c for c in cols if c in e.columns]
    return e.sort_values("signal_date", ascending=False).head(limit)[cols].to_dict("records")


GROUPINGS = ("regime", "score_bucket", "year", "sector", "symbol", "direction", "strategy_id", "exit_reason")


def _hist(values, bins) -> List[dict]:
    counts, edges = np.histogram(values, bins=bins)
    return [{"from": round(float(a), 2), "to": round(float(b), 2), "count": int(c)} for a, b, c in zip(edges[:-1], edges[1:], counts)]


def explore(events: pd.DataFrame, *, strategy_id: Optional[str] = None, regime: Optional[str] = None, score_bucket_: Optional[str] = None,
            sector: Optional[str] = None, symbol: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
            group_by: Optional[str] = None, sectors: Optional[Dict[str, str]] = None, min_group: int = 1) -> Dict:
    """Filterable target-hit statistics over historical events, with Wilson CIs and outcome distributions."""
    if events is None or events.empty:
        return {"sample_size": 0, "groups": [], "filters": {}}
    e = events.copy()
    e["year"] = pd.to_datetime(e["signal_date"]).dt.year.astype(str)
    e["sector"] = e["symbol"].map(sectors or {}).fillna("Unknown")
    filt = {"strategy_id": strategy_id, "regime": regime, "score_bucket": score_bucket_, "sector": sector, "symbol": symbol}
    for k, v in filt.items():
        if v:
            e = e[e[k] == v]
    if start:
        e = e[e["signal_date"] >= start]
    if end:
        e = e[e["exit_date"] <= end]
    overall = hit_rates(e.to_dict("records"))
    groups = []
    if group_by:
        if group_by not in GROUPINGS:
            raise ValueError(f"group_by must be one of {GROUPINGS}")
        for key, g in e.groupby(e[group_by].fillna("Unknown")):
            if len(g) >= min_group:
                groups.append({"key": str(key), **hit_rates(g.to_dict("records"))})
        groups.sort(key=lambda x: (x["key"] if group_by == "year" else -x["sample_size"]))
    dist = {}
    if len(e):
        dist = {
            "r_multiple": _hist(e["r_multiple"].clip(-3, 6), np.arange(-3, 6.5, 0.5)),
            "mfe_r": _hist(e["mfe_r"].clip(0, 6), np.arange(0, 6.5, 0.5)),
            "mae_r": _hist(e["mae_r"].clip(0, 3), np.arange(0, 3.25, 0.25)),
            "bars_held": _hist(e["bars_held"], np.arange(0, int(e["bars_held"].max()) + 3, 2)),
        }
        dist["pct_reaching_1r_mfe"] = round(100 * float((e["mfe_r"] >= 1).mean()), 1)
        dist["pct_heat_over_0_5r"] = round(100 * float((e["mae_r"] >= 0.5).mean()), 1)
    return {"filters": {k: v for k, v in {**filt, "start": start, "end": end}.items() if v}, "group_by": group_by,
            "overall": overall, "groups": groups, "distributions": dist,
            "period": [str(pd.to_datetime(e["signal_date"]).min().date()), str(pd.to_datetime(e["exit_date"]).max().date())] if len(e) else None,
            "note": "Historical outcomes of the stated rules. Past hit rates are evidence, not a forecast."}
