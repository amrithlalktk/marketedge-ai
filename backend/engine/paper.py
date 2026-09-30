"""Paper-trade execution: replays new bars against an order/position using exactly the
backtester's rules, so paper results are comparable with backtested statistics.

* Market order: fills at the OPEN of the first bar after the order time (+ slippage).
* Limit (buy below / sell above) and stop (buy above / sell below) entries fill when a bar
  trades through the price; a gap beyond the price fills at the open (never better than possible).
* Stop gapped through at the open → exit at the open; a bar touching both stop and target → stop first.
* Optional partial exit at T1, after which the stop on the remainder moves to breakeven.
* Commission on every fill notional; exit slippage on every exit.
* Orders not filled before `expires_at` are cancelled; positions past `max_hold_bars` exit at that close.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class PaperOrder:
    direction: str               # LONG | SHORT
    quantity: float
    order_type: str              # market | limit | stop
    created_at: pd.Timestamp
    limit_price: Optional[float] = None
    stop: Optional[float] = None
    target1: Optional[float] = None
    target2: Optional[float] = None
    partial_at_t1: float = 0.0   # fraction closed at T1 (0 = none)
    commission_pct: float = 0.12
    slippage_pct: float = 0.05
    expires_at: Optional[pd.Timestamp] = None
    max_hold_bars: Optional[int] = None
    # state
    status: str = "pending"      # pending | open | closed | cancelled
    filled_at: Optional[pd.Timestamp] = None
    filled_price: Optional[float] = None
    open_qty: float = 0.0
    t1_done: bool = False
    bars_held: int = 0
    realized: float = 0.0        # P&L in instrument currency, after costs
    costs: float = 0.0
    fills: List[dict] = field(default_factory=list)
    exit_reason: Optional[str] = None
    closed_at: Optional[pd.Timestamp] = None
    last_bar: Optional[pd.Timestamp] = None


def _fill(o: PaperOrder, kind: str, when, px: float, qty: float, entry: bool) -> None:
    sign = 1 if o.direction == "LONG" else -1
    slip = o.slippage_pct / 100
    price = px * (1 + sign * slip) if entry else px * (1 - sign * slip)
    fee = abs(price * qty) * o.commission_pct / 100
    o.costs += fee
    if entry:
        o.filled_price, o.filled_at, o.open_qty, o.status = price, when, qty, "open"
        o.realized -= fee
    else:
        o.realized += sign * (price - o.filled_price) * qty - fee
        o.open_qty -= qty
    o.fills.append({"kind": kind, "at": str(when), "price": round(price, 6), "quantity": qty, "fee": round(fee, 4)})


def _close(o: PaperOrder, reason: str, when, px: float) -> None:
    _fill(o, reason, when, px, o.open_qty, entry=False)
    o.status, o.exit_reason, o.closed_at = "closed", reason, when


def step(o: PaperOrder, bars: pd.DataFrame) -> List[dict]:
    """Advance the order through bars newer than its last processed bar. Returns the fills that happened."""
    before = len(o.fills)
    sign = 1 if o.direction == "LONG" else -1
    new = bars[bars.index > (o.last_bar if o.last_bar is not None else o.created_at)]
    for ts, b in new.iterrows():
        o.last_bar = ts
        if o.status in ("closed", "cancelled"):
            break
        if o.status == "pending":
            if o.expires_at is not None and ts > o.expires_at:
                o.status, o.exit_reason = "cancelled", "expired"
                break
            if o.order_type == "market":
                _fill(o, "entry", ts, float(b.open), o.quantity, True)
            else:
                lp = float(o.limit_price)
                buy_below = (o.order_type == "limit") == (sign == 1)  # long limit / short stop trigger on the way down
                if buy_below and b.low <= lp:
                    _fill(o, "entry", ts, min(float(b.open), lp), o.quantity, True)
                elif not buy_below and b.high >= lp:
                    _fill(o, "entry", ts, max(float(b.open), lp), o.quantity, True)
                else:
                    continue
            if o.status != "open":
                continue
        # position management on this bar (including the fill bar)
        o.bars_held += 1
        stop = o.stop
        if stop is not None and o.filled_at != ts and sign * (b.open - stop) <= 0:  # held overnight and gapped through the stop
            _close(o, "stop_gap" if not o.t1_done else "breakeven_gap", ts, float(b.open))
            break
        hit_stop = stop is not None and sign * ((b.low if sign == 1 else b.high) - stop) <= 0
        if hit_stop:  # conservative: stop first when a bar touches both
            _close(o, "stop" if not o.t1_done else "breakeven", ts, float(stop))
            break
        far = b.high if sign == 1 else b.low
        if o.target1 is not None and not o.t1_done and sign * (far - o.target1) >= 0:
            o.t1_done = True
            if o.partial_at_t1 > 0 and o.target2 is not None:
                q = round(o.open_qty * o.partial_at_t1, 8)
                if 0 < q < o.open_qty:
                    _fill(o, "target1_partial", ts, float(o.target1), q, entry=False)
                o.stop = o.filled_price  # breakeven on the remainder
            elif o.target2 is None:
                _close(o, "target1", ts, float(o.target1))
                break
        if o.target2 is not None and sign * (far - o.target2) >= 0:
            _close(o, "target2", ts, float(o.target2))
            break
        if o.max_hold_bars and o.bars_held >= o.max_hold_bars:
            _close(o, "time", ts, float(b.close))
            break
    return o.fills[before:]


def close_at_market(o: PaperOrder, bars: pd.DataFrame, requested_at: pd.Timestamp) -> Optional[dict]:
    """Manual close: fills at the open of the first bar after the request (same convention as entries)."""
    nxt = bars[bars.index > requested_at]
    if o.status == "pending":
        o.status, o.exit_reason = "cancelled", "cancelled_by_user"
        return None
    if o.status != "open" or nxt.empty:
        return None
    ts = nxt.index[0]
    _close(o, "manual", ts, float(nxt.iloc[0].open))
    o.last_bar = ts
    return o.fills[-1]


def mark_to_market(o: PaperOrder, last_price: float) -> Dict[str, float]:
    sign = 1 if o.direction == "LONG" else -1
    unreal = sign * (last_price - o.filled_price) * o.open_qty if o.status == "open" and o.filled_price else 0.0
    return {"unrealized": round(unreal, 4), "realized": round(o.realized, 4), "total": round(o.realized + unreal, 4)}
