"""Event-driven trade simulator.

Rules (identical for every historical and live setup):
* Signal is evaluated on the close of bar t. Levels are frozen at t.
* Entry at the open of bar t+1 plus slippage. The trade is SKIPPED if that
  open gaps more than `max_chase_atr` beyond the signal close, or through the stop.
* Bars are walked from the entry bar onwards. If a bar's open gaps through the
  stop, the fill is the open (worse than the stop). If a bar touches both the
  stop and a target, the STOP is assumed first (conservative; OHLC data cannot
  tell the intrabar order) and the trade is flagged `ambiguous`.
* Outcome flags are measured against the ORIGINAL stop:
    t1_hit  - T1 touched before the initial stop
    t2_hit  - T2 touched before the initial stop
    stop_hit - initial stop hit before T1 (full-loss outcome)
    neither - time exit after `max_hold_bars` with no T1 and no stop
* P&L follows the trade-management rule: close `partial_at_t1` at T1, move the
  stop on the remainder to breakeven, exit the remainder at T2 / breakeven /
  time exit. Costs + slippage are charged on every fill.
* One open position per symbol per strategy: overlapping signals are ignored.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

from .config import BacktestConfig, LevelConfig
from .levels import TradeLevels, compute_levels
from .strategies import StrategySpec


@dataclass
class TradeResult:
    symbol: str
    strategy_id: str
    direction: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry: float
    stop: float
    t1: float
    t2: float
    exit_price: float
    t1_hit: bool
    t2_hit: bool
    stop_hit: bool
    neither: bool
    exit_reason: str
    bars_held: int
    gross_return_pct: float
    net_return_pct: float
    r_multiple: float
    mfe_r: float
    mae_r: float
    t1_date: Optional[str] = None  # date the partial exit at T1 filled (for portfolio simulation)
    risk_atr: Optional[float] = None  # stop distance from the fill, in ATRs at the signal bar
    ambiguous: bool = False  # one bar touched both the stop and a target: order unknown, stop assumed (conservative)

    def to_dict(self) -> dict:
        return asdict(self)


def _d(ix) -> str:
    return str(pd.Timestamp(ix).date())


def simulate_trade(f: pd.DataFrame, t: int, direction: str, stop: float, t1: float, t2: float,
                   bt: BacktestConfig, max_chase_atr: float, symbol: str = "", strategy_id: str = "",
                   data_final: bool = False) -> Optional[TradeResult]:
    """`data_final=True` means the series will never get more bars (delisted instrument): a trade still
    open at the last bar is closed there (exit_reason "delisted") instead of being excluded — excluding
    it would silently drop the crash-into-delisting losses (survivorship bias)."""
    n = len(f)
    if t + 1 >= n:
        return None
    sign = 1 if direction == "LONG" else -1
    o, h, l, c = (f[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    ref, a = c[t], float(f["atr"].iloc[t])
    raw_open = o[t + 1]
    if sign * (raw_open - ref) > max_chase_atr * a:
        return None  # gapped too far beyond the signal: not chased
    if sign * (raw_open - stop) <= 0:
        return None  # opened through the stop: invalidated before entry
    slip = bt.costs.slippage_pct / 100
    entry = raw_open * (1 + sign * slip)
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    t1_hit = t2_hit = stop_hit = ambiguous = False
    t1_idx = None
    remaining = 1.0
    realized = 0.0  # sum(fraction * price-return in direction)
    cur_stop = stop
    exit_reason, exit_price, exit_idx = "time", None, None
    mfe = mae = 0.0
    last = min(n - 1, t + bt.max_hold_bars)

    for i in range(t + 1, last + 1):
        bar_o, bar_h, bar_l = o[i], h[i], l[i]
        fav = (bar_h - entry) if sign == 1 else (entry - bar_l)
        adv = (entry - bar_l) if sign == 1 else (bar_h - entry)
        mfe, mae = max(mfe, fav / risk), max(mae, adv / risk)

        # 1) gap through the active stop at the open
        if i > t + 1 and sign * (bar_o - cur_stop) <= 0:
            if not t1_hit:
                stop_hit = True
            realized += remaining * sign * (bar_o - entry) / entry
            remaining, exit_reason, exit_price, exit_idx = 0.0, ("stop_gap" if not t1_hit else "breakeven_gap"), bar_o, i
            break
        touched_stop = sign * (bar_l if sign == 1 else bar_h) <= sign * cur_stop
        touched_t1 = sign * ((bar_h if sign == 1 else bar_l) - t1) >= 0
        touched_t2 = sign * ((bar_h if sign == 1 else bar_l) - t2) >= 0

        if touched_stop:  # conservative: stop assumed before any target on the same bar
            ambiguous = ambiguous or bool(touched_t2 or (touched_t1 and not t1_hit))
            if not t1_hit:
                stop_hit = True
            realized += remaining * sign * (cur_stop - entry) / entry
            remaining, exit_reason, exit_price, exit_idx = 0.0, ("stop" if not t1_hit else "breakeven"), cur_stop, i
            break
        if touched_t1 and not t1_hit:
            t1_hit = True
            t1_idx = i
            part = bt.partial_at_t1 if bt.partial_at_t1 > 0 else 0.0
            realized += part * sign * (t1 - entry) / entry
            remaining -= part
            cur_stop = entry  # breakeven on the remainder
        if touched_t2:
            t2_hit = True
            realized += remaining * sign * (t2 - entry) / entry
            remaining, exit_reason, exit_price, exit_idx = 0.0, "target2", t2, i
            break

    if remaining > 0:
        exit_idx = last
        exit_price = c[last]
        realized += remaining * sign * (exit_price - entry) / entry
        exit_reason = "time" if last == t + bt.max_hold_bars else ("delisted" if data_final else "open")  # 'open' = data ended
    if exit_reason == "open":
        return None  # unresolved trades are excluded from statistics (no peeking past data end)

    # Commission on entry + exit notional (partial exits together sum to one exit leg).
    # Entry slippage is already in the entry price; exit slippage is charged here.
    cost = 2 * bt.costs.commission_pct / 100 + slip
    gross = realized
    net = gross - cost
    return TradeResult(
        symbol=symbol, strategy_id=strategy_id, direction=direction,
        signal_date=_d(f.index[t]), entry_date=_d(f.index[t + 1]), exit_date=_d(f.index[exit_idx]),
        entry=round(entry, 4), stop=round(stop, 4), t1=round(t1, 4), t2=round(t2, 4), exit_price=round(float(exit_price), 4),
        t1_hit=t1_hit, t2_hit=t2_hit, stop_hit=stop_hit, neither=(not t1_hit and not stop_hit),
        exit_reason=exit_reason, bars_held=int(exit_idx - t), gross_return_pct=round(100 * gross, 4),
        net_return_pct=round(100 * net, 4), r_multiple=round(net * entry / risk, 4),
        mfe_r=round(mfe, 3), mae_r=round(mae, 3),
        t1_date=_d(f.index[t1_idx]) if t1_idx is not None else None,
        risk_atr=round(risk / a, 3) if a > 0 else None, ambiguous=ambiguous,
    )


def backtest_symbol(f: pd.DataFrame, symbol: str, spec: StrategySpec, signals: pd.Series, bt: BacktestConfig,
                    lv: LevelConfig, start: Optional[pd.Timestamp] = None, end: Optional[pd.Timestamp] = None,
                    context: Optional[Callable[[int, TradeLevels], dict]] = None, data_final: bool = False) -> List[dict]:
    """Replay a strategy over one symbol's feature frame. `context(t, levels)` may
    attach signal-time attributes (regime, score bucket) for conditional statistics."""
    strategy_id = spec.id
    bt_local = BacktestConfig(max_hold_bars=spec.max_hold_bars, partial_at_t1=bt.partial_at_t1,
                              risk_per_trade_pct=bt.risk_per_trade_pct, costs=bt.costs)
    idx = np.flatnonzero(signals.to_numpy())
    trades: List[dict] = []
    busy_until: Optional[pd.Timestamp] = None
    for t in idx:
        ts = f.index[t]
        if start is not None and ts < start:
            continue
        if end is not None and ts > end:
            break
        if busy_until is not None and ts <= busy_until:
            continue
        levels = compute_levels(f, t, spec.direction, spec.levels or lv)
        if levels is None:
            continue
        tr = simulate_trade(f, t, spec.direction, levels.stop, levels.targets[0], levels.targets[1],
                            bt_local, lv.max_chase_atr, symbol, strategy_id, data_final=data_final)
        if tr is None:
            continue
        d = tr.to_dict()
        d["rr_t2_planned"] = levels.rr_t2
        if context is not None:
            d.update(context(t, levels))
        trades.append(d)
        busy_until = pd.Timestamp(tr.exit_date)
    return trades
