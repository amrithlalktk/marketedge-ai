"""ML market-regime model: a Gaussian mixture over trend/volatility features of the benchmark.

Shown ALONGSIDE the rule-based regime (with an agreement flag); it does not drive
any rule, score or backtest. States are named from their own feature means
(trend sign × volatility level), so labels are reproducible for a given history.
Point-in-time: the model is fitted only on bars up to (and including) the
evaluated date; the reported history is the in-sample classification.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture

from .. import indicators as ind

FAMILY_OF = {"Calm uptrend": "bull", "Volatile uptrend": "bull", "Calm drift / range": "range", "Volatile selloff": "stress", "Downtrend": "bear"}


def family_of(name: str):
    return FAMILY_OF.get(name.split(" (")[0])


def _features(close: pd.Series, high: pd.Series, low: pd.Series) -> pd.DataFrame:
    r = np.log(close).diff()
    return pd.DataFrame({
        "ret20": close.pct_change(20),
        "vol20": r.rolling(20).std() * np.sqrt(252),
        "dist200": close / ind.ema(close, 200) - 1,
        "adx": ind.adx(high, low, close)["adx"] / 100,
    }).dropna()


def _name(mean: Dict[str, float], vol_median: float) -> str:
    trend = mean["ret20"] + 0.5 * mean["dist200"]
    high_vol = mean["vol20"] > vol_median * 1.15
    if trend > 0.01:
        return "Volatile uptrend" if high_vol else "Calm uptrend"
    if trend < -0.01:
        return "Volatile selloff" if high_vol else "Downtrend"
    return "Calm drift / range"


def fit_predict(bench: pd.DataFrame, n_states: int = 4, rule_regime: Optional[dict] = None, seed: int = 0) -> Dict:
    X = _features(bench["close"], bench["high"], bench["low"])
    if len(X) < 250:
        return {"available": False, "note": "Not enough history for the ML regime model (needs ≥ 250 bars after warm-up)."}
    mu, sd = X.mean(), X.std().replace(0, 1)
    Z = ((X - mu) / sd).to_numpy()
    gm = GaussianMixture(n_components=n_states, covariance_type="full", random_state=seed, n_init=3).fit(Z)
    means = pd.DataFrame(gm.means_ * sd.to_numpy() + mu.to_numpy(), columns=X.columns)
    vol_median = float(X["vol20"].median())
    names = [_name(means.iloc[k].to_dict(), vol_median) for k in range(n_states)]
    for base in set(names):  # two clusters can share a description: qualify them by trend strength
        dup = [k for k, nm in enumerate(names) if nm == base]
        if len(dup) > 1:
            dup.sort(key=lambda k: -(means.iloc[k]["ret20"] + 0.5 * means.iloc[k]["dist200"]))
            quals = ["strong", "moderate", "mild", "weak"]
            for rank, k in enumerate(dup):
                names[k] = f"{base} ({quals[min(rank, 3)]})"
    probs = gm.predict_proba(Z)
    states = probs.argmax(axis=1)
    cur = probs[-1]
    agg: Dict[str, float] = {}
    for k, p in enumerate(cur):
        agg[names[k]] = agg.get(names[k], 0.0) + float(p)
    label = max(agg, key=agg.get)
    hist = pd.Series([names[s] for s in states], index=X.index)
    change = hist.ne(hist.shift())
    runs = hist[change]
    agree = None
    if rule_regime:
        risk_off = {"bear", "stress"}
        mine, theirs = family_of(label), rule_regime.get("family")
        agree = mine == theirs or (mine in risk_off and theirs in risk_off)
    return {
        "available": True, "as_of": str(X.index[-1].date()), "state": label, "confidence": round(agg[label], 3),
        "state_probabilities": {k: round(v, 3) for k, v in sorted(agg.items(), key=lambda kv: -kv[1])},
        "states": [{"name": names[k], "mean_ret20_pct": round(100 * float(means.iloc[k]["ret20"]), 2), "mean_vol20_pct": round(100 * float(means.iloc[k]["vol20"]), 1),
                    "mean_dist200_pct": round(100 * float(means.iloc[k]["dist200"]), 2), "share_of_history_pct": round(100 * float((states == k).mean()), 1)}
                   for k in range(n_states)],
        "recent_transitions": [{"date": str(d.date()), "state": s} for d, s in runs.iloc[-8:].items()],
        "rule_based_regime": rule_regime.get("regime") if rule_regime else None,
        "agrees_with_rule_based": agree,
        "method": "Gaussian mixture (4 states) on 20-day return, 20-day realised volatility, distance from 200 EMA and ADX",
        "note": "Context only: the ML regime does not change rules, scores or backtests. History shown is in-sample classification.",
    }
