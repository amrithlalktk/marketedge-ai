"""Losing-trade diagnostics and an honest replay of the publish gate.

Everything here works on the historical event set (one row per simulated trade, see backtest.simulate_trade)
and never changes a stored prediction:

* `gate_replay` walks history in time order and decides, for every historical trade, whether the live
  publish gate WOULD have published it on its signal date — using only trades that had already EXITED
  before that date (no look-ahead). The published subset is what a user would actually have received.
* `outcome_stats` / `by_group` / `loss_profile` describe what happened to a set of trades.
* `compare_policies` measures alternative gates on a design period and on a later out-of-sample period.
  A policy is preferred only on the design period; the out-of-sample numbers are reported, not tuned.
* `corporate_action_gaps` finds overnight price jumps that look like unadjusted splits/bonuses.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

LEVELS = (("strategy + regime + score bucket", ["strategy_id", "regime_family", "score_bucket"]),
          ("strategy + regime", ["strategy_id", "regime_family"]),
          ("strategy (all regimes)", ["strategy_id"]))


@dataclass
class GatePolicy:
    """The evidence part of the publish gate (validation.validate checks 9/10 plus the geometry checks)."""
    name: str
    min_sample: int = 30
    lcb_z: float = 0.0          # publish only if mean R − z·SE > 0 (0 = the plain "expectancy > 0" rule)
    levels: tuple = (0, 1, 2)   # which conditioning levels may be used (indices into LEVELS), most specific first
    min_score: float = 60.0
    min_rr: float = 2.0
    max_stop_pct: float = 10.0
    min_t1_r: float = 1.0
    use_score: bool = True      # apply the setup-score rule
    recent_min_n: int = 0       # >0: also require ≥ this many trades in the last RECENT_DAYS with a positive mean R

    def to_dict(self) -> dict:
        return {**asdict(self), "levels": [LEVELS[i][0] for i in self.levels]}


RECENT_DAYS = 365


def _prepare(events: pd.DataFrame) -> pd.DataFrame:
    e = events.copy()
    e["signal_ts"] = pd.to_datetime(e["signal_date"])
    e["exit_ts"] = pd.to_datetime(e["exit_date"])
    risk = (e["entry"] - e["stop"]).abs()
    e["stop_pct"] = 100 * risk / e["entry"]
    e["t1_r"] = (e["t1"] - e["entry"]).abs() / risk.where(risk > 0)
    return e.sort_values("signal_ts").reset_index(drop=True)


def prior_evidence(events: pd.DataFrame) -> pd.DataFrame:
    """For every trade and every conditioning level: n, mean R and SD of the comparable trades that EXITED
    strictly before its signal date (exactly what probability.estimate sees live)."""
    e = _prepare(events)
    out = pd.DataFrame(index=e.index)
    r = e["r_multiple"].to_numpy(dtype=float)
    hit1 = e["t1_hit"].astype(bool).to_numpy(dtype=float)
    for li, (_, cols) in enumerate(LEVELS):
        n = np.zeros(len(e), dtype=int)
        s1 = np.zeros(len(e))
        s2 = np.zeros(len(e))
        h1 = np.zeros(len(e))
        nr = np.zeros(len(e), dtype=int)
        s1r = np.zeros(len(e))
        valid = e[cols].notna().all(axis=1)
        for _, g in e[valid].groupby(cols, sort=False):
            order = np.argsort(g["exit_ts"].to_numpy())
            ex = g["exit_ts"].to_numpy()[order]
            rr = r[g.index.to_numpy()][order]
            hh = hit1[g.index.to_numpy()][order]
            c1, c2 = np.concatenate([[0.0], np.cumsum(rr)]), np.concatenate([[0.0], np.cumsum(rr * rr)])
            ch = np.concatenate([[0.0], np.cumsum(hh)])
            sig = g["signal_ts"].to_numpy()
            k = np.searchsorted(ex, sig, side="left")  # exits strictly before the signal
            k0 = np.searchsorted(ex, sig - np.timedelta64(RECENT_DAYS, "D"), side="left")
            n[g.index], s1[g.index], s2[g.index], h1[g.index] = k, c1[k], c2[k], ch[k]
            nr[g.index], s1r[g.index] = k - k0, c1[k] - c1[k0]
        mean = np.divide(s1, n, out=np.full(len(e), np.nan), where=n > 0)
        var = np.divide(s2, n, out=np.full(len(e), np.nan), where=n > 0) - mean ** 2
        sd = np.sqrt(np.clip(var * np.divide(n, n - 1, out=np.ones(len(e)), where=n > 1), 0, None))
        out[f"n{li}"], out[f"mean{li}"], out[f"sd{li}"] = n, mean, sd
        out[f"t1rate{li}"] = np.divide(h1, n, out=np.full(len(e), np.nan), where=n > 0)
        out[f"nr{li}"], out[f"meanr{li}"] = nr, np.divide(s1r, nr, out=np.full(len(e), np.nan), where=nr > 0)
    return pd.concat([e, out], axis=1)


def gate_replay(events: pd.DataFrame, policy: GatePolicy, evidence: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Adds `published` (bool), `evidence_level`, `evidence_n`, `evidence_mean_r` and `gate_failed` (first failing rule)."""
    e = evidence if evidence is not None else prior_evidence(events)
    e = e.copy()
    n_sel = np.zeros(len(e), dtype=int)
    m_sel = np.full(len(e), np.nan)
    sd_sel = np.full(len(e), np.nan)
    t1_sel = np.full(len(e), np.nan)
    nr_sel = np.zeros(len(e), dtype=int)
    mr_sel = np.full(len(e), np.nan)
    lvl = np.full(len(e), None, dtype=object)
    chosen = np.zeros(len(e), dtype=bool)
    for li in policy.levels:  # first level with enough evidence, like probability.estimate
        take = ~chosen & (e[f"n{li}"].to_numpy() >= policy.min_sample)
        n_sel[take], m_sel[take], sd_sel[take] = e[f"n{li}"].to_numpy()[take], e[f"mean{li}"].to_numpy()[take], e[f"sd{li}"].to_numpy()[take]
        t1_sel[take], nr_sel[take], mr_sel[take] = e[f"t1rate{li}"].to_numpy()[take], e[f"nr{li}"].to_numpy()[take], e[f"meanr{li}"].to_numpy()[take]
        lvl[take] = LEVELS[li][0]
        chosen |= take
    se = np.divide(sd_sel, np.sqrt(n_sel), out=np.full(len(e), np.nan), where=n_sel > 0)
    lcb = m_sel - policy.lcb_z * np.nan_to_num(se)
    regime = e.get("regime", pd.Series(None, index=e.index))
    bad_regime = ((e["direction"] == "LONG") & (regime == "Panic/Selloff")) | ((e["direction"] == "SHORT") & (regime == "Strong Bull"))
    rules = [
        ("Historical sample size", chosen),
        ("Backtest validity", np.nan_to_num(lcb, nan=-1) > 0),
        ("Recent performance", (nr_sel >= policy.recent_min_n) & (np.nan_to_num(mr_sel, nan=-1) > 0) if policy.recent_min_n else np.ones(len(e), dtype=bool)),
        ("Setup score", (e["score_at_signal"].fillna(0).to_numpy() >= policy.min_score) if policy.use_score else np.ones(len(e), dtype=bool)),
        ("Risk/reward", e["rr_t2_planned"].fillna(0).to_numpy() >= policy.min_rr),
        ("Stop width", e["stop_pct"].to_numpy() <= policy.max_stop_pct),
        ("Target distance", e["t1_r"].fillna(0).to_numpy() >= policy.min_t1_r),
        ("Market regime", ~bad_regime.to_numpy()),
    ]
    failed = np.full(len(e), None, dtype=object)
    ok = np.ones(len(e), dtype=bool)
    for name, passed in rules:
        failed[ok & ~passed] = name
        ok &= passed
    e["published"], e["gate_failed"] = ok, failed
    e["evidence_level"], e["evidence_n"], e["evidence_mean_r"] = lvl, n_sel, np.round(m_sel, 3)
    e["predicted_t1_rate"] = np.round(100 * t1_sel, 1)
    return e


def calibration(df: pd.DataFrame) -> Dict:
    """Shown chance (prior Target-1 rate of the evidence used) vs what then happened, by predicted band."""
    d = df.dropna(subset=["predicted_t1_rate"])
    if d.empty:
        return {"status": "no data"}
    bands = pd.cut(d["predicted_t1_rate"], [0, 25, 35, 45, 55, 100], include_lowest=True)
    rows = [{"predicted": str(k), "trades": int(len(g)), "avg_predicted": round(float(g["predicted_t1_rate"].mean()), 1),
             "actual": round(100 * float(g["t1_hit"].astype(bool).mean()), 1)} for k, g in d.groupby(bands, observed=True) if len(g)]
    gap = float(d["predicted_t1_rate"].mean() - 100 * d["t1_hit"].astype(bool).mean())
    return {"avg_predicted_t1": round(float(d["predicted_t1_rate"].mean()), 1), "actual_t1": round(100 * float(d["t1_hit"].astype(bool).mean()), 1),
            "overstatement_pts": round(gap, 1), "status": "calibrated" if abs(gap) <= 3 else ("overstated" if gap > 0 else "understated"),
            "bands": rows}


def max_drawdown_r(df: pd.DataFrame) -> Optional[float]:
    """Largest peak-to-trough fall of cumulative R, trades booked on their exit date (1 R risked per trade)."""
    if df.empty:
        return None
    eq = df.sort_values("exit_ts" if "exit_ts" in df else "exit_date")["r_multiple"].cumsum()
    return round(float((eq.cummax().clip(lower=0) - eq).max()), 2)


def outcome_stats(df: pd.DataFrame) -> Dict:
    n = len(df)
    if n == 0:
        return {"trades": 0}
    r = df["r_multiple"].astype(float)
    wins, losses = r[r > 0].sum(), -r[r <= 0].sum()
    out = {
        "trades": n,
        "t1_rate": round(100 * df["t1_hit"].mean(), 1), "t2_rate": round(100 * df["t2_hit"].mean(), 1),
        "stop_rate": round(100 * df["stop_hit"].mean(), 1), "time_exit_rate": round(100 * df["neither"].mean(), 1),
        "win_rate": round(100 * float((r > 0).mean()), 1),
        "expectancy_r": round(float(r.mean()), 3), "expectancy_se": round(float(r.std(ddof=1) / np.sqrt(n)), 3) if n > 1 else None,
        "profit_factor": round(float(wins / losses), 2) if losses > 0 else None,
        "max_drawdown_r": max_drawdown_r(df),
        "avg_net_return_pct": round(float(df["net_return_pct"].mean()), 2),
        "avg_gross_return_pct": round(float(df["gross_return_pct"].mean()), 2),
        "avg_bars_held": round(float(df["bars_held"].mean()), 1),
        "avg_mfe_r": round(float(df["mfe_r"].mean()), 2), "avg_mae_r": round(float(df["mae_r"].mean()), 2),
    }
    if "ambiguous" in df and df["ambiguous"].notna().any():
        out["ambiguous_rate"] = round(100 * float(df["ambiguous"].fillna(False).astype(bool).mean()), 1)
    return out


def loss_profile(df: pd.DataFrame) -> Dict:
    """How the stopped-out trades failed — the evidence for or against each usual explanation."""
    stops = df[df["stop_hit"].astype(bool)]
    winners = df[df["t1_hit"].astype(bool)]
    n = len(stops)
    if n == 0:
        return {"stopped_trades": 0}
    out = {
        "stopped_trades": n,
        "fast_failures_pct": round(100 * float((stops["bars_held"] <= 2).mean()), 1),          # wrong entry / false breakout
        "gap_through_stop_pct": round(100 * float((stops["exit_reason"] == "stop_gap").mean()), 1),  # overnight risk
        "were_in_profit_0_5r_pct": round(100 * float((stops["mfe_r"] >= 0.5).mean()), 1),     # target too far / no trailing
        "were_in_profit_1r_pct": round(100 * float((stops["mfe_r"] >= 1.0).mean()), 1),
        "avg_r_lost": round(float(stops["r_multiple"].mean()), 2),
        "median_bars_to_stop": float(stops["bars_held"].median()),
    }
    if "risk_atr" in df and df["risk_atr"].notna().any():
        out["median_stop_atr_stopped"] = round(float(stops["risk_atr"].median()), 2)
        out["median_stop_atr_winners"] = round(float(winners["risk_atr"].median()), 2) if len(winners) else None
        tight = df["risk_atr"] < 1.0
        out["stop_rate_tight_lt_1atr"] = round(100 * float(df.loc[tight, "stop_hit"].mean()), 1) if tight.any() else None
        out["stop_rate_normal_ge_1atr"] = round(100 * float(df.loc[~tight, "stop_hit"].mean()), 1) if (~tight).any() else None
    if "ambiguous" in df and df["ambiguous"].notna().any():
        out["ambiguous_same_bar_pct"] = round(100 * float(stops["ambiguous"].fillna(False).astype(bool).mean()), 1)
    return out


def by_group(df: pd.DataFrame, col: str, min_trades: int = 20) -> List[Dict]:
    if col not in df:
        return []
    rows = [{"key": str(k), **outcome_stats(g)} for k, g in df.groupby(df[col].fillna("Unknown")) if len(g) >= min_trades]
    return sorted(rows, key=lambda x: -x["trades"])


def compare_policies(events: pd.DataFrame, policies: List[GatePolicy], split: Optional[str] = None) -> Dict:
    """Published-subset results for each policy, before (design) and after (out-of-sample) `split`.
    Default split: 60% of the period. The same evidence table is reused, so policies differ only in their rules."""
    ev = prior_evidence(events)
    if split is None:
        lo, hi = ev["signal_ts"].min(), ev["signal_ts"].max()
        split = str((lo + (hi - lo) * 0.6).date())
    s = pd.Timestamp(split)
    rows = []
    for p in policies:
        g = gate_replay(events, p, ev)
        pub = g[g["published"]]
        oos = pub[pub["signal_ts"] >= s]
        rows.append({"policy": p.to_dict(),
                     "design": outcome_stats(pub[pub["signal_ts"] < s]), "out_of_sample": outcome_stats(oos),
                     "out_of_sample_by_strategy": by_group(oos, "strategy_id", min_trades=10),
                     "out_of_sample_calibration": calibration(oos),
                     "rejected_out_of_sample": outcome_stats(g[(~g["published"]) & (g["signal_ts"] >= s)])})
    return {"split": split, "period": [str(ev["signal_ts"].min().date()), str(ev["exit_ts"].max().date())], "policies": rows}


# ------------------------------------------------------------------ data audit
SPLIT_RATIOS = (1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 2 / 3, 3 / 4, 4 / 5, 2 / 5, 1 / 6, 1 / 8)


def corporate_action_gaps(bars: Dict[str, pd.DataFrame], min_jump: float = 0.25, tol: float = 0.02) -> pd.DataFrame:
    """Overnight jumps (open vs previous close) of more than `min_jump` that stay (the next close does not undo them)
    and sit within `tol` of a common split/bonus ratio — most likely unadjusted corporate actions, not news."""
    rows = []
    for sym, df in bars.items():
        if df is None or len(df) < 3:
            continue
        c, o = df["close"].astype(float), df["open"].astype(float)
        ratio = o / c.shift(1)
        stays = (c / c.shift(1) - ratio).abs() < 0.15
        for ts in ratio.index[((ratio - 1).abs() >= min_jump) & stays]:
            x = float(ratio.loc[ts])
            near = min(SPLIT_RATIOS, key=lambda k: abs(x - k))
            if abs(x - near) / near <= tol:
                rows.append({"symbol": sym, "date": str(pd.Timestamp(ts).date()), "ratio": round(x, 3), "looks_like": f"1:{round(1 / near, 2):g}"})
    return pd.DataFrame(rows, columns=["symbol", "date", "ratio", "looks_like"])


def trades_spanning(events: pd.DataFrame, gaps: pd.DataFrame, lookback_days: int = 365) -> pd.Series:
    """True for trades whose holding window — or the year of indicator history before the signal — contains a suspected
    unadjusted corporate action on the same symbol (their indicators, levels or outcome may be distorted)."""
    if gaps.empty or events.empty:
        return pd.Series(False, index=events.index)
    by_sym = {s: pd.to_datetime(g["date"]).to_numpy() for s, g in gaps.groupby("symbol")}
    hit = np.zeros(len(events), dtype=bool)
    sig, ex = pd.to_datetime(events["signal_date"]).to_numpy(), pd.to_datetime(events["exit_date"]).to_numpy()
    for i, sym in enumerate(events["symbol"].to_numpy()):
        d = by_sym.get(sym)
        if d is not None:
            hit[i] = bool(((d > sig[i] - np.timedelta64(lookback_days, "D")) & (d <= ex[i])).any())
    return pd.Series(hit, index=events.index)
