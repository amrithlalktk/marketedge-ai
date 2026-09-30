"""Market breadth and sector rotation computed from a universe of feature frames.

Point-in-time: at each date only instruments that had a bar on that date count,
so delisted instruments are included while they traded (no survivorship bias).
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def breadth_series(features: Dict[str, pd.DataFrame], membership: Optional[Dict[str, pd.Series]] = None) -> pd.DataFrame:
    """Market breadth across `features`; with `membership`, each date counts only that date's
    point-in-time universe members (engine.universe)."""
    cols = {}
    for sym, f in features.items():
        if membership is not None:
            if sym not in membership:
                continue
            m = membership[sym].reindex(f.index, fill_value=False).astype(bool)
            f = f.where(m)  # non-member rows become NaN → comparisons False, valid* False
        cols[sym] = pd.DataFrame({
            "a20": f["close"] > f["sma20"], "a50": f["close"] > f["sma50"], "a200": f["close"] > f["sma200"],
            "valid200": f["sma200"].notna(), "valid50": f["sma50"].notna(), "valid20": f["sma20"].notna(),
            "adv": f["ret1"] > 0, "dec": f["ret1"] < 0,
            "nh": f["high"] >= f["high_52w"], "nl": f["low"] <= f["low_52w"],
            "upvol": f["volume"].where(f["ret1"] > 0, 0.0), "dnvol": f["volume"].where(f["ret1"] < 0, 0.0),
        })
    if not cols:
        return pd.DataFrame()
    panel = pd.concat(cols, axis=1)
    get = lambda k: panel.xs(k, axis=1, level=1).astype(float)
    def pct(a, v):
        num = (get(a) * get(v)).sum(axis=1)
        den = get(v).sum(axis=1).replace(0, np.nan)
        return 100 * num / den
    adv, dec = get("adv").sum(axis=1), get("dec").sum(axis=1)
    up, dn = get("upvol").sum(axis=1), get("dnvol").sum(axis=1)
    return pd.DataFrame({
        "pct_above_20": pct("a20", "valid20"), "pct_above_50": pct("a50", "valid50"), "pct_above_200": pct("a200", "valid200"),
        "advances": adv, "declines": dec, "ad_ratio": adv / dec.replace(0, np.nan),
        "new_highs": get("nh").sum(axis=1), "new_lows": get("nl").sum(axis=1),
        "up_volume_pct": 100 * up / (up + dn).replace(0, np.nan),
        "members": get("valid20").sum(axis=1),
    })


def breadth_snapshot(b: pd.DataFrame) -> Dict:
    b = b.dropna(subset=["pct_above_20"])
    if b.empty:
        return {}
    last = b.iloc[-1]
    prev = b.iloc[-6] if len(b) > 6 else last
    r = lambda x: None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 2)
    return {
        "as_of": str(b.index[-1].date()),
        "pct_above_20dma": r(last["pct_above_20"]), "pct_above_50dma": r(last["pct_above_50"]), "pct_above_200dma": r(last["pct_above_200"]),
        "advances": int(last["advances"]), "declines": int(last["declines"]), "ad_ratio": r(last["ad_ratio"]),
        "new_highs": int(last["new_highs"]), "new_lows": int(last["new_lows"]), "up_volume_pct": r(last["up_volume_pct"]),
        "pct_above_50dma_change_5d": r(last["pct_above_50"] - prev["pct_above_50"]),
        "members": int(last["members"]),
        "history": [[str(d.date()), r(v)] for d, v in b["pct_above_50"].iloc[-120:].items()],
    }


def sector_rotation(features: Dict[str, pd.DataFrame], sectors: Dict[str, str], benchmark: Optional[pd.Series] = None) -> list:
    """Rank sectors by equal-weight relative strength, momentum, breadth, volume trend
    and trend state. Scores are cross-sectional z-scores: they describe the
    present, not a forecast."""
    by_sector: Dict[str, list] = {}
    for sym, f in features.items():
        sec = sectors.get(sym)
        if sec:
            by_sector.setdefault(sec, []).append(f)
    bench63 = float(benchmark.pct_change(63).iloc[-1]) if benchmark is not None and len(benchmark) > 63 else 0.0
    rows = []
    for sec, frames in by_sector.items():
        last = [f.iloc[-1] for f in frames if len(f) > 63]
        if not last:
            continue
        rows.append({
            "sector": sec, "members": len(last),
            "ret20_pct": 100 * float(np.nanmean([x["ret20"] for x in last])),
            "ret63_pct": 100 * float(np.nanmean([x["ret63"] for x in last])),
            "relative_strength_63d": 100 * (float(np.nanmean([x["ret63"] for x in last])) - bench63),
            "pct_above_50dma": 100 * float(np.mean([x["close"] > x["sma50"] for x in last])),
            "volume_trend": (float(np.mean(vt)) if (vt := [x["vol_sma20"] / x["vol_sma50"] for x in last if x["vol_sma50"] and x["vol_sma50"] > 0]) else None),
            "pct_uptrend": 100 * float(np.mean([(x["ema20"] > x["ema50"]) and (x["ema50"] > x["ema200"]) for x in last])),
        })
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["volume_trend"] = pd.to_numeric(df["volume_trend"], errors="coerce")
    z = lambda s: ((s - s.mean()) / (s.std(ddof=0) or 1)).fillna(0.0)  # missing metric (e.g. no volume in FX) → neutral
    df["score"] = (0.35 * z(df["relative_strength_63d"]) + 0.25 * z(df["ret20_pct"]) + 0.2 * z(df["pct_above_50dma"])
                   + 0.1 * z(df["volume_trend"]) + 0.1 * z(df["pct_uptrend"]))
    df = df.sort_values("score", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    return [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in df.to_dict("records")]
