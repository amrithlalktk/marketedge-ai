"""Portfolios: paper execution through the broker adapter, journal entries, valuation in base currency, analytics."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers import PaperBroker
from app.models import Instrument, Portfolio, PortfolioTrade
from app.services.market_data import load_bars
from engine.journal import analytics
from engine.paper import PaperOrder

BROKER = PaperBroker()
_STATE = ("open_qty", "t1_done", "bars_held", "fills", "last_bar", "exit_reason")


def _naive(ts) -> Optional[pd.Timestamp]:
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    return t.tz_convert(None) if t.tzinfo else t


def to_order(t: PortfolioTrade) -> PaperOrder:
    o = PaperOrder(direction=t.direction, quantity=t.quantity, order_type=t.order_type, created_at=_naive(t.order_at), limit_price=t.limit_price,
                   stop=t.stop, target1=t.target1, target2=t.target2, partial_at_t1=t.partial_at_t1, commission_pct=t.commission_pct,
                   slippage_pct=t.slippage_pct, expires_at=_naive(t.expires_at), max_hold_bars=t.max_hold_bars, status=t.status,
                   filled_at=_naive(t.filled_at), filled_price=t.filled_price, realized=t.realized, costs=t.costs, closed_at=_naive(t.closed_at))
    st = t.state or {}
    o.open_qty, o.t1_done, o.bars_held = st.get("open_qty", 0.0), st.get("t1_done", False), st.get("bars_held", 0)
    o.fills, o.exit_reason = list(st.get("fills", [])), st.get("exit_reason")
    o.last_bar = _naive(st.get("last_bar"))
    return o


def from_order(t: PortfolioTrade, o: PaperOrder) -> None:
    t.status, t.filled_price, t.realized, t.costs, t.stop = o.status, o.filled_price, o.realized, o.costs, o.stop
    t.filled_at = o.filled_at.to_pydatetime() if o.filled_at is not None else None
    t.closed_at = o.closed_at.to_pydatetime() if o.closed_at is not None else None
    t.exit_reason = o.exit_reason
    if o.status == "closed" and o.fills:
        t.exit_price = o.fills[-1]["price"]
    d = asdict(o)
    t.state = {k: (str(d[k]) if k == "last_bar" and d[k] is not None else d[k]) for k in _STATE}


def latest_bar_time(db: Session, ins: Instrument) -> Optional[pd.Timestamp]:
    df = load_bars(db, {ins.id: ins.symbol}).get(ins.symbol)
    return df.index[-1] if df is not None and len(df) else None


def _same_currency(p: Portfolio, ins: Instrument) -> None:
    """The lite build has no FX feed, so a portfolio holds one currency (INR for NSE, USD for crypto)."""
    ccy = "USD" if (ins.currency or "") in ("USD", "USDT") else (ins.currency or "INR")
    if ccy != p.base_currency:
        raise ValueError(f"{ins.symbol} trades in {ccy} but this portfolio is in {p.base_currency}. "
                         f"Use a {ccy} portfolio (no currency conversion in the lite build).")


def load_fx_book(db: Session):  # kept for call sites: same-currency portfolios need no conversion
    return None


def place(db: Session, p: Portfolio, ins: Instrument, spec: dict) -> PortfolioTrade:
    """Paper order. The order time is the latest stored bar: it fills from the NEXT bar on (never the bar it was placed on)."""
    from app.services.settings_service import engine_config

    _same_currency(p, ins)
    ref = latest_bar_time(db, ins)
    if ref is None:
        raise ValueError(f"No market data for {ins.symbol}")
    cfg = engine_config(db, market=ins.market)
    t = PortfolioTrade(portfolio_id=p.id, instrument_id=ins.id, symbol=ins.symbol, market=ins.market, currency=ins.currency,
                       direction=spec["direction"], order_type=spec.get("order_type", "market"), status="pending", quantity=float(spec["quantity"]),
                       limit_price=spec.get("limit_price"), stop=spec.get("stop"), target1=spec.get("target1"), target2=spec.get("target2"),
                       partial_at_t1=float(spec.get("partial_at_t1", 0.0)), commission_pct=cfg.backtest.costs.commission_pct,
                       slippage_pct=cfg.backtest.costs.slippage_pct, max_hold_bars=spec.get("max_hold_bars"),
                       expires_at=spec.get("expires_at"), signal_id=spec.get("signal_id"), notes=spec.get("notes", ""),
                       order_at=ref.to_pydatetime(), state={"open_qty": 0.0, "fills": []})
    db.add(t)
    db.commit()
    return t


def journal_entry(db: Session, p: Portfolio, ins: Instrument, spec: dict) -> PortfolioTrade:
    """User-entered real trade (no simulation): entry/exit/qty/brokerage/taxes as reported by the user."""
    _same_currency(p, ins)
    sign = 1 if spec["direction"] == "LONG" else -1
    closed = spec.get("exit_price") is not None
    gross = sign * (spec["exit_price"] - spec["entry_price"]) * spec["quantity"] if closed else 0.0
    t = PortfolioTrade(portfolio_id=p.id, instrument_id=ins.id, symbol=ins.symbol, market=ins.market, currency=ins.currency,
                       direction=spec["direction"], order_type="journal", status="closed" if closed else "open", quantity=float(spec["quantity"]),
                       stop=spec.get("stop"), target1=spec.get("target1"), target2=spec.get("target2"), commission_pct=0.0, slippage_pct=0.0,
                       brokerage=float(spec.get("brokerage", 0)), taxes=float(spec.get("taxes", 0)), filled_price=spec["entry_price"],
                       filled_at=spec["entry_at"], exit_price=spec.get("exit_price"), closed_at=spec.get("exit_at"), exit_reason="journal" if closed else None,
                       realized=gross, notes=spec.get("notes", ""), order_at=spec["entry_at"],
                       state={"open_qty": 0.0 if closed else float(spec["quantity"]), "fills": []})
    db.add(t)
    db.commit()
    return t


def process(db: Session, trade_ids: Optional[List[int]] = None) -> Dict[str, int]:
    """Advance every pending/open paper trade with any new bars (idempotent; safe to run on every tick)."""
    q = select(PortfolioTrade).join(Portfolio).where(Portfolio.kind == "paper", PortfolioTrade.status.in_(("pending", "open")))
    if trade_ids:
        q = q.where(PortfolioTrade.id.in_(trade_ids))
    trades = list(db.scalars(q))
    by_ins: Dict[int, List[PortfolioTrade]] = {}
    for t in trades:
        by_ins.setdefault(t.instrument_id, []).append(t)
    events = {"trades": len(trades), "fills": 0}
    fx = None
    for iid, ts in by_ins.items():
        sym = ts[0].symbol
        bars = load_bars(db, {iid: sym}).get(sym)
        if bars is None:
            continue
        for t in ts:
            o = to_order(t)
            before_status = o.status
            if t.close_requested_at is not None:
                BROKER.close(o, bars, _naive(t.close_requested_at))
            else:
                BROKER.advance(o, bars)
            new = len(o.fills) - len((t.state or {}).get("fills", []))
            events["fills"] += max(new, 0)
            if o.filled_at is not None and t.fx_to_base_entry is None:
                fx = fx or load_fx_book(db)
                t.fx_to_base_entry = _rate(fx, t.currency, _base(t))
            if o.status == "closed" and t.fx_to_base_exit is None:
                fx = fx or load_fx_book(db)
                t.fx_to_base_exit = _rate(fx, t.currency, _base(t))
            from_order(t, o)
            if new > 0 or o.status != before_status:
                _trade_notifications(db, t, o.fills[-new:] if new > 0 else [], before_status)
    db.commit()
    return events


def _base(t: PortfolioTrade) -> str:
    return "INR"  # portfolios report in INR; the base currency is fixed per portfolio at creation


def _rate(fx, ccy: str, base: str) -> float:
    if ccy == base or (ccy == "USDT" and base == "USD") or fx is None:
        return 1.0  # same-currency portfolios only (enforced when a trade is added)
    c = fx.convert(ccy, base)
    return c["rate"] if c else 1.0


def _trade_notifications(db: Session, t: PortfolioTrade, fills: List[dict], before: str) -> None:
    from app.services.notify_service import notify

    p = db.get(Portfolio, t.portfolio_id)
    labels = {"entry": "filled", "target1_partial": "T1 partial exit", "target1": "T1 reached", "target2": "T2 reached", "stop": "stop hit",
              "stop_gap": "stop hit (gap)", "breakeven": "breakeven stop hit", "breakeven_gap": "breakeven stop hit (gap)", "time": "time exit",
              "manual": "closed at market"}
    for f in fills:
        notify(db, p.user_id, f"Paper trade {t.symbol}: {labels.get(f['kind'], f['kind'])}",
               f"{t.direction} {f['quantity']:g} {t.symbol} @ {f['price']:g} ({p.name})", link=f"/portfolio?id={p.id}",
               payload={"trade_id": t.id, "fill": f}, deliver_now=True)
    if t.status == "cancelled" and before == "pending":
        notify(db, p.user_id, f"Paper order {t.symbol} cancelled", f"{t.exit_reason or 'cancelled'} ({p.name})", link=f"/portfolio?id={p.id}")


def valuation(db: Session, p: Portfolio) -> Dict:
    fx = load_fx_book(db)
    ids = {t.instrument_id: t.symbol for t in p.trades}
    bars = load_bars(db, ids) if ids else {}
    rows, closed, marks = [], [], []
    for t in p.trades:
        df = bars.get(t.symbol)
        last = float(df["close"].iloc[-1]) if df is not None and len(df) else None
        last_at = str(df.index[-1].date()) if df is not None and len(df) else None
        base_rate = _rate(fx, t.currency, p.base_currency)
        sign = 1 if t.direction == "LONG" else -1
        open_qty = float((t.state or {}).get("open_qty", 0.0))
        unreal = sign * (last - t.filled_price) * open_qty if t.status == "open" and last is not None and t.filled_price else 0.0
        extra_costs = t.brokerage + t.taxes  # journal costs already in base currency
        realized_base = t.realized * (t.fx_to_base_exit or base_rate) - (extra_costs if t.status == "closed" else 0.0)
        risk = abs((t.filled_price or 0) - (t.stop or t.filled_price or 0)) * open_qty * base_rate if t.status == "open" and t.stop else 0.0
        row = {"id": t.id, "symbol": t.symbol, "market": t.market, "currency": t.currency, "direction": t.direction, "status": t.status,
               "order_type": t.order_type, "quantity": t.quantity, "open_quantity": open_qty, "limit_price": t.limit_price, "stop": t.stop,
               "target1": t.target1, "target2": t.target2, "filled_price": t.filled_price, "filled_at": str(t.filled_at) if t.filled_at else None,
               "exit_price": t.exit_price, "closed_at": str(t.closed_at) if t.closed_at else None, "exit_reason": t.exit_reason,
               "last_price": last, "last_price_at": last_at, "unrealized": round(unreal, 4), "realized": round(t.realized, 4),
               "costs": round(t.costs, 4), "brokerage": t.brokerage, "taxes": t.taxes, "fx_to_base": base_rate,
               "unrealized_base": round(unreal * base_rate, 2), "realized_base": round(realized_base, 2),
               "holding_days": (pd.Timestamp(t.closed_at or datetime.now(timezone.utc).replace(tzinfo=None)) - pd.Timestamp(t.filled_at)).days if t.filled_at else None,
               "fills": (t.state or {}).get("fills", []), "signal_id": t.signal_id, "notes": t.notes, "close_requested": t.close_requested_at is not None}
        rows.append(row)
        if t.status == "closed" and t.filled_at:
            closed.append({"pnl": realized_base, "entry_at": t.filled_at, "exit_at": t.closed_at})
        elif t.status == "open":
            ins = db.get(Instrument, t.instrument_id)
            marks.append({"unrealized": unreal * base_rate, "exposure": (last or t.filled_price or 0) * open_qty * base_rate, "risk": risk,
                          "market": t.market, "sector": ins.sector.name if ins and ins.sector else None})
    stats = analytics(closed, p.starting_capital, marks)
    return {"id": p.id, "name": p.name, "kind": p.kind, "base_currency": p.base_currency, "starting_capital": p.starting_capital,
            "trades": rows, "analytics": stats,
            "execution_note": ("Paper fills follow the backtest rules: market orders fill at the next bar's open, stops that gap fill at the open, "
                               "and a bar touching stop and target counts as the stop. Evaluated on stored bars (EOD unless an intraday feed is configured)."
                               if p.kind == "paper" else "Journal: trades as entered by you; no simulated execution.")}
