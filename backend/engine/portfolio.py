"""Portfolio-level backtest simulator.

Replays the trade list produced by the event backtester through a single
capital account, day by day:

* Sizing uses equity as of the PREVIOUS close (no look-ahead):
  qty = min(risk budget / risk-per-share, max position % of equity / price, available cash / price)
* Capacity: at most `max_positions` open; one position per symbol; optional
  per-sector exposure cap. When more signals arrive than slots, the higher
  signal-time score wins (ties: symbol order) — the same information a trader had.
* Fills: entry price already includes entry slippage; exits (partial at T1 and
  final) pay exit slippage; commission is charged on every fill's notional.
* Equity is marked to market at every close; shorts are modelled as
  margin-blocked notional with P&L = qty × (entry − price).
* Skipped trades are counted by reason, so the gap between "strategy" and
  "portfolio" results is visible.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


TRADE_LOG_LIMIT = 5000  # most recent portfolio fills kept in the stored result


@dataclass
class PortfolioConfig:
    initial_capital: float = 1_000_000.0
    risk_per_trade_pct: float = 1.0
    max_positions: int = 10
    max_position_pct: float = 20.0
    max_sector_pct: Optional[float] = None
    partial_at_t1: float = 0.5
    commission_pct: float = 0.12
    slippage_pct: float = 0.05
    periods_per_year: int = 252

    def to_dict(self):
        return asdict(self)


@dataclass
class _Pos:
    symbol: str
    sector: Optional[str]
    direction: int
    qty: int
    entry_px: float
    stop: float
    entry_date: pd.Timestamp
    trade: dict
    realized: float = 0.0
    costs: float = 0.0
    partial_done: bool = False


def _resample_last(s: pd.Series, new: str, old: str) -> pd.Series:
    try:
        return s.resample(new).last()
    except ValueError:  # pandas < 2.2
        return s.resample(old).last()


def simulate(trades: List[dict], closes: Dict[str, pd.Series], cfg: PortfolioConfig,
             sectors: Optional[Dict[str, str]] = None, benchmark: Optional[pd.Series] = None) -> Dict:
    if not trades:
        return {"trades_taken": 0, "note": "No trades to simulate"}
    sectors = sectors or {}
    comm, slip = cfg.commission_pct / 100, cfg.slippage_pct / 100
    by_entry: Dict[pd.Timestamp, List[dict]] = defaultdict(list)
    for t in trades:
        by_entry[pd.Timestamp(t["entry_date"])].append(t)
    start = min(by_entry)
    end = max(pd.Timestamp(t["exit_date"]) for t in trades)
    cal = sorted({d for s in closes.values() for d in s.index if start <= d <= end} | set(by_entry))
    last_close: Dict[str, float] = {}
    close_maps = {k: v.to_dict() for k, v in closes.items()}

    cash = cfg.initial_capital
    open_pos: Dict[str, _Pos] = {}
    equity_prev = cfg.initial_capital
    curve, exposure, npos = [], [], []
    log, skipped = [], defaultdict(int)
    total_costs = turnover = 0.0
    max_concurrent = 0

    def exit_fill(p: _Pos, px: float, frac_qty: int, when: pd.Timestamp, reason: str):
        nonlocal cash, total_costs, turnover
        fill = px * (1 - slip) if p.direction == 1 else px * (1 + slip)
        notional = fill * frac_qty
        c = notional * comm
        pnl = p.direction * (fill - p.entry_px) * frac_qty
        cash += p.entry_px * frac_qty + pnl - c  # release blocked notional + P&L
        p.realized += pnl
        p.costs += c
        total_costs += c
        turnover += notional
        p.qty -= frac_qty
        if p.qty == 0:
            log.append({"symbol": p.symbol, "strategy_id": p.trade.get("strategy_id"), "direction": "LONG" if p.direction == 1 else "SHORT",
                        "entry_date": str(p.entry_date.date()), "exit_date": str(when.date()), "entry_px": round(p.entry_px, 2),
                        "exit_px": round(fill, 2), "qty": p.trade["_qty"], "exit_reason": reason, "pnl": round(p.realized - p.costs, 2),
                        "costs": round(p.costs, 2), "return_pct": round(100 * (p.realized - p.costs) / (p.entry_px * p.trade["_qty"]), 3)})

    def process_exits(d: pd.Timestamp, symbols) -> None:
        for sym in list(symbols):
            p = open_pos[sym]
            t = p.trade
            if not p.partial_done and t.get("t1_date") and pd.Timestamp(t["t1_date"]) == d and cfg.partial_at_t1 > 0:
                q = int(math.floor(p.qty * cfg.partial_at_t1))
                if 0 < q < p.qty:
                    exit_fill(p, float(t["t1"]), q, d, "t1_partial")
                p.partial_done = True
            if pd.Timestamp(t["exit_date"]) <= d:
                exit_fill(p, float(t["exit_price"]), p.qty, d, t["exit_reason"])
                del open_pos[sym]

    for d in cal:
        # 1) exits scheduled for today on positions held overnight (partial at T1 first, then final)
        process_exits(d, list(open_pos))
        entered_today = []
        # 2) entries at today's open, sized on yesterday's equity
        cands = sorted(by_entry.get(d, []), key=lambda t: (-(t.get("score_at_signal") or 0), t["symbol"]))
        for t in cands:
            sym = t["symbol"]
            if sym in open_pos:
                skipped["already_holding_symbol"] += 1
                continue
            if len(open_pos) >= cfg.max_positions:
                skipped["max_positions"] += 1
                continue
            px, stop = float(t["entry"]), float(t["stop"])
            rpu = abs(px - stop)
            if rpu <= 0:
                skipped["invalid_levels"] += 1
                continue
            qty = min(equity_prev * cfg.risk_per_trade_pct / 100 / rpu, equity_prev * cfg.max_position_pct / 100 / px, cash / (px * (1 + comm)))
            sec = sectors.get(sym)
            if cfg.max_sector_pct and sec:
                used = sum(p.entry_px * p.qty for p in open_pos.values() if p.sector == sec)
                qty = min(qty, max(equity_prev * cfg.max_sector_pct / 100 - used, 0) / px)
            qty = int(math.floor(qty))
            if qty < 1:
                skipped["insufficient_capital_or_sector_cap"] += 1
                continue
            c = px * qty * comm
            cash -= px * qty + c
            total_costs += c
            turnover += px * qty
            t = {**t, "_qty": qty}
            open_pos[sym] = _Pos(sym, sec, 1 if t["direction"] == "LONG" else -1, qty, px, stop, d, t, costs=c)
            entered_today.append(sym)
            max_concurrent = max(max_concurrent, len(open_pos))
        # trades that hit T1 / stop on their entry bar resolve the same day
        process_exits(d, entered_today)
        # 3) mark to market at the close
        value, gross = 0.0, 0.0
        for sym, p in open_pos.items():
            px = close_maps.get(sym, {}).get(d)
            if px is not None and px == px:
                last_close[sym] = float(px)
            mark = last_close.get(sym, p.entry_px)
            value += p.entry_px * p.qty + p.direction * (mark - p.entry_px) * p.qty
            gross += mark * p.qty
        equity = cash + value
        curve.append((d, equity))
        exposure.append(100 * gross / equity if equity > 0 else 0.0)
        npos.append(len(open_pos))
        equity_prev = equity

    eq = pd.Series([v for _, v in curve], index=[d for d, _ in curve])
    dr = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    peak = eq.cummax()
    dd = (eq / peak - 1) * 100
    downside = dr[dr < 0]
    pnl = np.array([x["pnl"] for x in log]) if log else np.array([])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    monthly = _resample_last(eq, "ME", "M").pct_change().dropna()
    yearly = _resample_last(eq, "YE", "Y")
    yr = yearly.pct_change()
    if len(yr):
        yr.iloc[0] = yearly.iloc[0] / cfg.initial_capital - 1
    bench = None
    if benchmark is not None:
        b = benchmark.reindex(eq.index).ffill().dropna()
        if len(b) > 1:
            bench = {"total_return_pct": round(100 * float(b.iloc[-1] / b.iloc[0] - 1), 2),
                     "curve": [[str(k.date()), round(float(v / b.iloc[0] * cfg.initial_capital), 0)] for k, v in b.iloc[:: max(1, len(b) // 400)].items()]}
    step = max(1, len(eq) // 400)
    return {
        "config": cfg.to_dict(),
        "period": [str(eq.index[0].date()), str(eq.index[-1].date())],
        "final_equity": round(float(eq.iloc[-1]), 2),
        "total_return_pct": round(100 * float(eq.iloc[-1] / cfg.initial_capital - 1), 2),
        "cagr_pct": round(100 * ((float(eq.iloc[-1]) / cfg.initial_capital) ** (1 / years) - 1), 2),
        "max_drawdown_pct": round(float(dd.min()), 2),
        "sharpe": round(float(np.sqrt(cfg.periods_per_year) * dr.mean() / dr.std()), 2) if dr.std() > 0 else None,
        "sortino": round(float(np.sqrt(cfg.periods_per_year) * dr.mean() / downside.std()), 2) if len(downside) > 1 and downside.std() > 0 else None,
        "trades_taken": len(log), "trades_available": len(trades), "skipped": dict(skipped),
        "win_rate": round(100 * len(wins) / len(pnl), 1) if len(pnl) else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) and losses.sum() < 0 else None,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else None,
        "expectancy_per_trade": round(float(pnl.mean()), 2) if len(pnl) else None,
        "total_costs": round(total_costs, 2), "turnover": round(turnover, 2),
        "avg_exposure_pct": round(float(np.mean(exposure)), 1), "max_concurrent_positions": max_concurrent,
        "equity_curve": [[str(k.date()), round(float(v), 0)] for k, v in eq.iloc[::step].items()],
        "drawdown_curve": [[str(k.date()), round(float(v), 2)] for k, v in dd.iloc[::step].items()],
        "exposure_curve": [[str(k.date()), round(float(v), 1), int(n)] for (k, v, n) in list(zip(eq.index, exposure, npos))[::step]],
        "monthly_returns": {k.strftime("%Y-%m"): round(100 * float(v), 2) for k, v in monthly.items()},
        "yearly_returns": {str(k.year): round(100 * float(v), 2) for k, v in yr.items()},
        "benchmark": bench,
        "trade_log": log[-TRADE_LOG_LIMIT:],
        "trade_log_total": len(log),
        "trade_log_truncated": len(log) > TRADE_LOG_LIMIT,
        "open_at_end": len(open_pos),
        "method": ("Daily mark-to-market single-account simulation; sizing on prior-close equity; max positions, "
                   "one position per symbol, optional sector cap; commission on every fill; exit slippage on every exit."),
    }
