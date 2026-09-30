"""Portfolio / trade-journal analytics (closed trades + open marks), in the portfolio's base currency."""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def analytics(closed: List[dict], starting_capital: float, open_marks: List[dict] = None) -> Dict:
    """closed: [{pnl, entry_at, exit_at, sector?, market?}] in base currency; open_marks: [{unrealized, exposure, risk, sector, market}]."""
    open_marks = open_marks or []
    pnl = np.array([t["pnl"] for t in closed], dtype=float) if closed else np.array([])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    hold = [(pd.Timestamp(t["exit_at"]) - pd.Timestamp(t["entry_at"])).days for t in closed if t.get("exit_at") and t.get("entry_at")]
    out: Dict = {
        "total_trades": len(closed), "winning_trades": int(len(wins)), "losing_trades": int(len(losses)),
        "win_rate": round(100 * len(wins) / len(pnl), 1) if len(pnl) else None,
        "avg_winner": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loser": round(float(losses.mean()), 2) if len(losses) else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) and losses.sum() < 0 else None,
        "expectancy": round(float(pnl.mean()), 2) if len(pnl) else None,
        "realized_pnl": round(float(pnl.sum()), 2) if len(pnl) else 0.0,
        "unrealized_pnl": round(float(sum(m["unrealized"] for m in open_marks)), 2),
        "avg_holding_days": round(float(np.mean(hold)), 1) if hold else None,
        "open_positions": len(open_marks),
        "open_risk": round(float(sum(m.get("risk") or 0 for m in open_marks)), 2),
        "gross_exposure": round(float(sum(abs(m.get("exposure") or 0) for m in open_marks)), 2),
    }
    equity = starting_capital + out["realized_pnl"] + out["unrealized_pnl"]
    out["equity"] = round(equity, 2)
    out["return_pct"] = round(100 * (equity / starting_capital - 1), 2) if starting_capital else None
    out["open_risk_pct"] = round(100 * out["open_risk"] / equity, 2) if equity else None
    out["exposure_by_market"] = _group(open_marks, "market")
    out["exposure_by_sector"] = _group(open_marks, "sector")
    if closed:
        df = pd.DataFrame(closed)
        df["exit_at"] = pd.to_datetime(df["exit_at"])
        daily = df.groupby(df["exit_at"].dt.normalize())["pnl"].sum().sort_index()
        eq = starting_capital + daily.cumsum()
        curve = pd.concat([pd.Series([starting_capital], index=[daily.index[0] - pd.Timedelta(days=1)]), eq])
        dd = curve / curve.cummax() - 1
        out["max_drawdown_pct"] = round(100 * float(dd.min()), 2)
        rets = curve.pct_change().dropna()
        out["sharpe"] = round(float(np.sqrt(252) * rets.mean() / rets.std()), 2) if len(rets) > 2 and rets.std() > 0 else None
        out["equity_curve"] = [[str(d.date()), round(float(v), 2)] for d, v in curve.items()]
        out["monthly_pnl"] = {k.strftime("%Y-%m"): round(float(v), 2) for k, v in df.groupby(df["exit_at"].dt.to_period("M"))["pnl"].sum().items()}
    else:
        out.update({"max_drawdown_pct": None, "sharpe": None, "equity_curve": [], "monthly_pnl": {}})
    out["note"] = ("Sharpe and drawdown use realised P&L by exit date (open positions are marked separately); "
                   "few trades make these statistics unreliable.")
    return out


def _group(marks: List[dict], key: str) -> Dict[str, float]:
    g: Dict[str, float] = {}
    for m in marks:
        k = m.get(key) or "Unknown"
        g[k] = round(g.get(k, 0.0) + abs(m.get("exposure") or 0.0), 2)
    return g
