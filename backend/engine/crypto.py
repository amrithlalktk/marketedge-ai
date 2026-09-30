"""Crypto derivatives context (perpetual futures).

Exchange APIs expose only short histories of open interest and long/short
ratios (≈30 days), too short to backtest. So derivatives data is used as
CONTEXT and WARNINGS on a setup, never as a gate or a score input, and the
payload says so.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .validation import Check

FUNDING_HOT = 0.0005    # 0.05% per 8h ≈ 55% annualised: crowded
FUNDING_COLD = -0.0003


def derivatives_context(close: pd.Series, deriv: Optional[pd.DataFrame], direction: Optional[str] = None) -> Dict:
    """deriv: DatetimeIndex frame with any of funding_rate (per 8h, decimal), open_interest (contracts or USD),
    long_short_ratio. Returns interpretation + warning checks for `direction`."""
    if deriv is None or deriv.empty:
        return {"available": False, "note": "No perpetual-futures data for this instrument from the configured provider.", "checks": []}
    d = deriv.sort_index()
    out: Dict = {"available": True, "as_of": str(d.index[-1].date()),
                 "note": "Derivatives history is too short to backtest; shown as context and warnings only (not used in the score or hit rates)."}
    checks: List[Check] = []
    fr = d["funding_rate"].dropna() if "funding_rate" in d else pd.Series(dtype=float)
    if len(fr):
        now, avg7 = float(fr.iloc[-1]), float(fr.iloc[-7:].mean())
        out["funding"] = {"latest_pct_8h": round(100 * now, 4), "avg_7d_pct_8h": round(100 * avg7, 4),
                          "annualised_pct": round(100 * now * 3 * 365, 1),
                          "state": "crowded longs" if now >= FUNDING_HOT else "crowded shorts" if now <= FUNDING_COLD else "neutral"}
        if direction == "LONG" and now >= FUNDING_HOT:
            checks.append(Check("Funding crowding", False, "warn", f"Funding {100 * now:.3f}%/8h — longs are paying heavily; squeeze risk against new longs"))
        if direction == "SHORT" and now <= FUNDING_COLD:
            checks.append(Check("Funding crowding", False, "warn", f"Funding {100 * now:.3f}%/8h — shorts are crowded; short-squeeze risk"))
    oi = d["open_interest"].dropna() if "open_interest" in d else pd.Series(dtype=float)
    if len(oi) >= 2:
        c = close.reindex(oi.index, method="ffill")
        ch1 = float(oi.iloc[-1] / oi.iloc[-2] - 1)
        ch7 = float(oi.iloc[-1] / oi.iloc[max(0, len(oi) - 8)] - 1)
        p7 = float(c.iloc[-1] / c.iloc[max(0, len(c) - 8)] - 1) if len(c) >= 2 else np.nan
        if np.isnan(p7):
            interp = "n/a"
        elif p7 > 0 and ch7 > 0:
            interp = "Price up with rising OI — new longs entering (trend confirmation)"
        elif p7 > 0 and ch7 <= 0:
            interp = "Price up with falling OI — short covering; weaker follow-through"
        elif p7 <= 0 and ch7 > 0:
            interp = "Price down with rising OI — new shorts entering"
        else:
            interp = "Price down with falling OI — long liquidation / deleveraging"
        out["open_interest"] = {"latest": float(oi.iloc[-1]), "change_1d_pct": round(100 * ch1, 2), "change_7d_pct": round(100 * ch7, 2),
                                "price_change_7d_pct": None if np.isnan(p7) else round(100 * p7, 2), "interpretation": interp}
        if direction == "LONG" and p7 > 0 and ch7 < -0.05:
            checks.append(Check("Open interest", False, "warn", "Rally on falling open interest (short covering) — breakouts on OI decline follow through less often"))
        if direction == "SHORT" and p7 < 0 and ch7 < -0.05:
            checks.append(Check("Open interest", False, "warn", "Decline on falling open interest (deleveraging) — may be exhausting"))
    ls = d["long_short_ratio"].dropna() if "long_short_ratio" in d else pd.Series(dtype=float)
    if len(ls):
        v = float(ls.iloc[-1])
        out["long_short_ratio"] = {"latest": round(v, 3), "state": "long-heavy" if v >= 2 else "short-heavy" if v <= 0.8 else "balanced",
                                   "note": "Account-count ratio of top exchange traders; contrarian at extremes."}
        if direction == "LONG" and v >= 2.5:
            checks.append(Check("Positioning", False, "warn", f"Long/short ratio {v:.2f} — positioning already very long"))
        if direction == "SHORT" and v <= 0.7:
            checks.append(Check("Positioning", False, "warn", f"Long/short ratio {v:.2f} — positioning already very short"))
    out["liquidations"] = {"available": False, "note": "Liquidation data not available from the configured provider."}
    out["checks"] = [c.to_dict() for c in checks]
    return out


def btc_dominance(market_caps: Dict[str, float], btc_symbol: str) -> Optional[float]:
    total = sum(v for v in market_caps.values() if v)
    return round(100 * market_caps[btc_symbol] / total, 2) if total and market_caps.get(btc_symbol) else None
