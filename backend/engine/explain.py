"""Human-readable explanations built strictly from computed values."""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .features import active_patterns


def _p(x) -> str:
    """Format a price with precision suited to its magnitude (index, stock, FX pair or sub-cent token)."""
    from .levels import price_decimals

    return f"{x:.{price_decimals(abs(x))}f}"


def _f(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def explain(row: pd.Series, direction: str, levels, prob: Dict, regime: Dict, mtf: Dict, checks, spec) -> Dict:
    sign = 1 if direction == "LONG" else -1
    c = float(row["close"])
    agree: List[str] = []
    disagree: List[str] = []

    def side(cond: bool, yes: str, no: str):
        (agree if cond else disagree).append(yes if cond else no)

    for n in (20, 50, 200):
        e = float(row[f"ema{n}"])
        side(sign * (c - e) > 0, f"Price {'above' if sign == 1 else 'below'} {n} EMA ({_p(e)})", f"Price {'below' if sign == 1 else 'above'} {n} EMA ({_p(e)})")
    r = float(row["rsi"])
    side((r > 50) == (sign == 1), f"RSI = {r:.0f}", f"RSI = {r:.0f} (against direction)")
    a = float(row["adx"])
    side(a >= 20, f"ADX = {a:.0f} (trend present)", f"ADX = {a:.0f} (weak trend)")
    side(sign * float(row["macd_hist"]) > 0, "MACD histogram confirms", "MACD histogram disagrees")
    vr = float(row["vol_ratio20"])
    has_volume = np.isfinite(float(row.get("vol_sma20", np.nan))) and float(row.get("vol_sma20", 0) or 0) > 0
    if has_volume:
        side(vr >= 1.2, f"Volume = {vr:.1f}× 20-day average", f"Volume = {vr:.1f}× average (no expansion)")
        side(sign * float(row.get("obv_slope", 0) or 0) > 0, "OBV trending with price", "OBV diverging from price")
    if regime:
        fam_ok = (regime["family"] == "bull") == (sign == 1)
        side(fam_ok, f"Market regime: {regime['regime']}", f"Market regime: {regime['regime']} (headwind)")
    if mtf:
        side(mtf["alignment"] in ("STRONG", "MODERATE"), f"Multi-timeframe alignment {mtf['alignment']}", f"Multi-timeframe alignment {mtf['alignment']}")
    agree.append(f"Risk/reward = 1:{levels.rr_t2:.1f} to T2")

    risks = [c_.detail for c_ in checks if not c_.passed and c_.severity == "warn"]
    if (sign == 1 and r >= 70) or (sign == -1 and r <= 30):
        risks.append(f"RSI {r:.0f} is stretched ({'overbought' if sign == 1 else 'oversold'})")
    if float(row.get("atr_pct_rank", 0) or 0) >= 0.85:
        risks.append("Volatility elevated (ATR in top 15% of the past year)")
    if levels.resistances and sign == 1:
        risks.append(f"Nearest resistance {_p(levels.resistances[0])}")
    if levels.supports and sign == -1:
        risks.append(f"Nearest support {_p(levels.supports[0])}")

    invalidation = [
        f"Daily close {'below' if sign == 1 else 'above'} the stop {_p(levels.stop)} ({levels.stop_method})",
        f"Next open gapping more than 0.5×ATR beyond {_p(levels.reference_price)} (entry would be skipped)",
    ]
    if mtf:
        invalidation.append("Weekly trend turning against the trade on a completed weekly close")

    hist = (f"{prob.get('sample_size', 0)} comparable historical trades ({prob.get('conditioning', 'n/a')}), "
            f"{prob['backtest_period'][0]} → {prob['backtest_period'][1]}" if prob.get("backtest_period") else "No comparable historical trades")
    return {
        "trigger": spec.description,
        "conditions_met": spec.conditions,
        "patterns": active_patterns(row),
        "agreeing": agree,
        "disagreeing": disagree,
        "risk_factors": risks,
        "invalidation": invalidation,
        "support": levels.supports,
        "resistance": levels.resistances,
        "historical_basis": hist,
    }
