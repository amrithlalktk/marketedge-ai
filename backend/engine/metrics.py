"""Performance statistics for a list of trades (dicts from backtest.simulate_trade)."""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def wilson_interval(successes: int, n: int, z: float = 1.96):
    if n == 0:
        return (None, None)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1))


def hit_rates(trades: List[dict]) -> Dict:
    n = len(trades)
    if n == 0:
        return {"sample_size": 0}
    t1 = sum(t["t1_hit"] for t in trades)
    t2 = sum(t["t2_hit"] for t in trades)
    st = sum(t["stop_hit"] for t in trades)
    ne = sum(t["neither"] for t in trades)
    wins = [t["net_return_pct"] for t in trades if t["net_return_pct"] > 0]
    losses = [t["net_return_pct"] for t in trades if t["net_return_pct"] <= 0]
    return {
        "sample_size": n,
        "t1_hits": t1, "t2_hits": t2, "stop_hits": st, "neither": ne,
        "t1_hit_rate": round(100 * t1 / n, 1), "t1_ci95": wilson_interval(t1, n),
        "t2_hit_rate": round(100 * t2 / n, 1), "t2_ci95": wilson_interval(t2, n),
        "stop_rate": round(100 * st / n, 1), "stop_ci95": wilson_interval(st, n),
        "neither_rate": round(100 * ne / n, 1),
        "avg_return_pct": round(float(np.mean([t["net_return_pct"] for t in trades])), 2),
        "avg_win_pct": round(float(np.mean(wins)), 2) if wins else None,
        "avg_loss_pct": round(float(np.mean(losses)), 2) if losses else None,
        "expectancy_r": round(float(np.mean([t["r_multiple"] for t in trades])), 3),
        "avg_holding_bars": round(float(np.mean([t["bars_held"] for t in trades])), 1),
    }


def equity_curve(trades: List[dict], risk_pct: float = 1.0, start_equity: float = 100.0) -> pd.Series:
    """Fixed-fractional equity: each trade changes equity by r_multiple × risk_pct,
    booked on its exit date. Concurrent positions are not capital-constrained
    (documented approximation; a portfolio simulator is on the roadmap)."""
    if not trades:
        return pd.Series(dtype=float)
    df = pd.DataFrame(trades)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df = df.sort_values(["exit_date", "entry_date"])
    eq = start_equity
    points = []
    for _, r in df.iterrows():
        eq *= 1 + r["r_multiple"] * risk_pct / 100
        points.append((r["exit_date"], eq))
    s = pd.Series([p[1] for p in points], index=[p[0] for p in points])
    return s.groupby(level=0).last()


def _resample_last(s: pd.Series, new_alias: str, old_alias: str) -> pd.Series:
    try:
        return s.resample(new_alias).last()
    except ValueError:  # pandas < 2.2
        return s.resample(old_alias).last()


def max_drawdown(eq: pd.Series) -> float:
    if eq.empty:
        return 0.0
    peak = eq.cummax()
    return round(float(100 * ((eq / peak) - 1).min()), 2)


def summary(trades: List[dict], risk_pct: float = 1.0, period_start: Optional[str] = None, period_end: Optional[str] = None,
            periods_per_year: int = 252) -> Dict:
    """`periods_per_year`: 252 for weekday markets, 365 for 24x7 (crypto) — sets the daily grid and annualisation."""
    base = hit_rates(trades)
    n = base.get("sample_size", 0)
    if n == 0:
        return {**base, "equity_curve": [], "monthly_returns": {}, "yearly_returns": {}}
    rets = np.array([t["net_return_pct"] for t in trades])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    eq = equity_curve(trades, risk_pct)
    start = pd.Timestamp(period_start) if period_start else eq.index.min()
    end = pd.Timestamp(period_end) if period_end else eq.index.max()
    grid = pd.date_range(start, end, freq="D") if periods_per_year >= 365 else pd.bdate_range(start, end)
    daily = eq.reindex(grid).ffill().fillna(100.0)
    dr = daily.pct_change().dropna()
    years = max((end - start).days / 365.25, 1e-9)
    final = float(eq.iloc[-1])
    downside = dr[dr < 0]
    ann = np.sqrt(periods_per_year)
    sharpe = float(ann * dr.mean() / dr.std()) if dr.std() > 0 else None
    sortino = float(ann * dr.mean() / downside.std()) if len(downside) > 1 and downside.std() > 0 else None
    monthly = _resample_last(daily, "ME", "M").pct_change().dropna()
    yearly = _resample_last(daily, "YE", "Y")
    yearly_ret = yearly.pct_change()
    if len(yearly):
        yearly_ret.iloc[0] = yearly.iloc[0] / 100.0 - 1
    best = max(trades, key=lambda t: t["net_return_pct"])
    worst = min(trades, key=lambda t: t["net_return_pct"])
    return {
        **base,
        "total_trades": n,
        "winning_trades": int(len(wins)), "losing_trades": int(len(losses)),
        "win_rate": round(100 * len(wins) / n, 1),
        "profit_factor": round(float(gross_win / gross_loss), 2) if gross_loss > 0 else None,
        "expectancy_pct": round(float(rets.mean()), 3),
        "best_trade": {k: best[k] for k in ("symbol", "signal_date", "net_return_pct")},
        "worst_trade": {k: worst[k] for k in ("symbol", "signal_date", "net_return_pct")},
        "cagr_pct": round(100 * ((final / 100.0) ** (1 / years) - 1), 2),
        "total_return_pct": round(final - 100.0, 2),
        "max_drawdown_pct": max_drawdown(daily),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "sortino": round(sortino, 2) if sortino is not None else None,
        "risk_per_trade_pct": risk_pct,
        "equity_curve": [[str(d.date()), round(float(v), 3)] for d, v in daily.iloc[:: max(1, len(daily) // 400)].items()],
        "monthly_returns": {str(d.strftime("%Y-%m")): round(100 * float(v), 2) for d, v in monthly.items()},
        "yearly_returns": {str(d.year): round(100 * float(v), 2) for d, v in yearly_ret.items()},
    }


def monte_carlo(trades: List[dict], risk_pct: float = 1.0, runs: int = 1000, seed: int = 7) -> Dict:
    """Bootstrap the trade sequence to show path dependence of drawdown and return."""
    if len(trades) < 10:
        return {"runs": 0, "note": "Too few trades for Monte Carlo"}
    rng = np.random.default_rng(seed)
    r = np.array([t["r_multiple"] for t in trades]) * risk_pct / 100
    finals, dds = [], []
    for _ in range(runs):
        path = np.cumprod(1 + rng.choice(r, size=len(r), replace=True))
        peak = np.maximum.accumulate(path)
        finals.append(100 * (path[-1] - 1))
        dds.append(100 * ((path / peak) - 1).min())
    return {
        "runs": runs,
        "total_return_pct": {"p5": round(float(np.percentile(finals, 5)), 2), "p50": round(float(np.percentile(finals, 50)), 2), "p95": round(float(np.percentile(finals, 95)), 2)},
        "max_drawdown_pct": {"p5": round(float(np.percentile(dds, 5)), 2), "p50": round(float(np.percentile(dds, 50)), 2), "p95": round(float(np.percentile(dds, 95)), 2)},
        "prob_loss_pct": round(100 * float(np.mean(np.array(finals) < 0)), 1),
    }
