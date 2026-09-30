from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import permissions_of, require
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Instrument, Portfolio, PortfolioTrade, Signal, User
from app.services import portfolio_service

router = APIRouter(prefix="/portfolios", tags=["portfolio & paper trading"])
DISCLAIMER = "Paper trading is simulated: no orders reach any broker and no money moves. Simulated results do not guarantee real results."


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="paper", pattern="^(paper|journal)$")
    starting_capital: float = Field(default=1_000_000, ge=1_000, le=1e11)


class OrderIn(BaseModel):
    symbol: str = Field(max_length=64)
    direction: str = Field(pattern="^(LONG|SHORT)$")
    quantity: float = Field(gt=0, le=1e9)
    order_type: str = Field(default="market", pattern="^(market|limit|stop)$")
    limit_price: Optional[float] = Field(default=None, gt=0)
    stop: Optional[float] = Field(default=None, gt=0)
    target1: Optional[float] = Field(default=None, gt=0)
    target2: Optional[float] = Field(default=None, gt=0)
    partial_at_t1: float = Field(default=0.0, ge=0, lt=1)
    max_hold_bars: Optional[int] = Field(default=None, ge=1, le=250)
    expires_at: Optional[datetime] = None
    signal_id: Optional[int] = None
    notes: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _levels(self):
        if self.order_type != "market" and self.limit_price is None:
            raise ValueError("limit/stop orders need limit_price")
        s = 1 if self.direction == "LONG" else -1
        ref = self.limit_price
        if ref is not None:
            if self.stop is not None and s * (ref - self.stop) <= 0:
                raise ValueError("stop must be on the losing side of the entry")
            for t in (self.target1, self.target2):
                if t is not None and s * (t - ref) <= 0:
                    raise ValueError("targets must be on the winning side of the entry")
        if self.target1 and self.target2 and s * (self.target2 - self.target1) <= 0:
            raise ValueError("target2 must be beyond target1")
        return self


class JournalIn(BaseModel):
    symbol: str = Field(max_length=64)
    direction: str = Field(pattern="^(LONG|SHORT)$")
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    entry_at: datetime
    exit_price: Optional[float] = Field(default=None, gt=0)
    exit_at: Optional[datetime] = None
    stop: Optional[float] = Field(default=None, gt=0)
    target1: Optional[float] = Field(default=None, gt=0)
    target2: Optional[float] = Field(default=None, gt=0)
    brokerage: float = Field(default=0.0, ge=0)
    taxes: float = Field(default=0.0, ge=0)
    notes: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _exit(self):
        if (self.exit_price is None) != (self.exit_at is None):
            raise ValueError("exit_price and exit_at go together")
        if self.exit_at and self.exit_at < self.entry_at:
            raise ValueError("exit_at must be after entry_at")
        return self


class TradePatch(BaseModel):
    stop: Optional[float] = Field(default=None, gt=0)
    target1: Optional[float] = Field(default=None, gt=0)
    target2: Optional[float] = Field(default=None, gt=0)
    notes: Optional[str] = Field(default=None, max_length=1000)


def _own(db: Session, pid: int, user: User) -> Portfolio:
    p = db.get(Portfolio, pid)
    if p is None or p.user_id != user.id:
        raise HTTPException(404, "Portfolio not found")
    return p


def _ins(db: Session, symbol: str) -> Instrument:
    ins = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if ins is None or ins.is_index:
        raise HTTPException(404, f"Unknown tradable symbol {symbol}")
    return ins


@router.get("")
def list_portfolios(db: Session = Depends(get_db), user: User = Depends(require("portfolio:write"))):
    return {"items": [portfolio_service.valuation(db, p) | {"trades": None} for p in db.scalars(select(Portfolio).where(Portfolio.user_id == user.id))],
            "disclaimer": DISCLAIMER}


@router.post("", status_code=201)
def create_portfolio(body: PortfolioIn, db: Session = Depends(get_db), user: User = Depends(require("portfolio:write"))):
    n = db.scalar(select(func.count()).select_from(Portfolio).where(Portfolio.user_id == user.id, Portfolio.kind == body.kind))
    if "portfolio:unlimited" not in permissions_of(user) and n >= get_settings().paper_portfolios_max_standard:
        raise HTTPException(403, f"Standard accounts can have {get_settings().paper_portfolios_max_standard} {body.kind} portfolio(s)")
    if db.scalar(select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.name == body.name)):
        raise HTTPException(409, "A portfolio with this name already exists")
    p = Portfolio(user_id=user.id, name=body.name, kind=body.kind, starting_capital=body.starting_capital)
    db.add(p)
    db.commit()
    return portfolio_service.valuation(db, p)


@router.get("/{pid}")
def get_portfolio(pid: int, db: Session = Depends(get_db), user: User = Depends(require("portfolio:write"))):
    return {**portfolio_service.valuation(db, _own(db, pid, user)), "disclaimer": DISCLAIMER}


@router.delete("/{pid}", status_code=204)
def delete_portfolio(pid: int, db: Session = Depends(get_db), user: User = Depends(require("portfolio:write"))):
    db.delete(_own(db, pid, user))
    db.commit()


@router.post("/{pid}/orders", status_code=201)
def place_order(pid: int, body: OrderIn, db: Session = Depends(get_db), user: User = Depends(require("portfolio:write"))):
    p = _own(db, pid, user)
    if p.kind != "paper":
        raise HTTPException(422, "Orders are for paper portfolios; use /journal for a trade journal")
    try:
        t = portfolio_service.place(db, p, _ins(db, body.symbol), body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    portfolio_service.process(db, [t.id])  # fills immediately if a newer bar already exists
    return {"trade_id": t.id, "status": db.get(PortfolioTrade, t.id).status,
            "note": "Order fills at the open of the next bar after " + str(t.order_at.date()) + " (same rule as the backtests)."}


@router.post("/{pid}/orders/from-setup/{signal_id}", status_code=201)
def order_from_setup(pid: int, signal_id: int, quantity: float = Query(gt=0), db: Session = Depends(get_db), user: User = Depends(require("portfolio:write"))):
    _own(db, pid, user)
    sig = db.get(Signal, signal_id)
    if sig is None or sig.market == "NFO":
        raise HTTPException(404, "Setup not found")
    s = sig.payload
    body = OrderIn(symbol=s["symbol"], direction=s["direction"], quantity=quantity, stop=s["stop"], target1=s["targets"][0], target2=s["targets"][1],
                   partial_at_t1=0.5, max_hold_bars=s["strategy"].get("max_hold_bars"), signal_id=signal_id,
                   notes=f"From setup #{signal_id}: {s['strategy']['name']} ({s['status']})")
    return place_order(pid, body, db, user)


@router.post("/{pid}/journal", status_code=201)
def add_journal(pid: int, body: JournalIn, db: Session = Depends(get_db), user: User = Depends(require("portfolio:write"))):
    p = _own(db, pid, user)
    if p.kind != "journal":
        raise HTTPException(422, "Journal entries go into a journal portfolio")
    spec = body.model_dump()
    for k in ("entry_at", "exit_at"):
        if spec[k] is not None and spec[k].tzinfo is not None:
            spec[k] = spec[k].replace(tzinfo=None)
    t = portfolio_service.journal_entry(db, p, _ins(db, body.symbol), spec)
    return {"trade_id": t.id, "status": t.status}


def _trade(db: Session, p: Portfolio, tid: int) -> PortfolioTrade:
    t = db.get(PortfolioTrade, tid)
    if t is None or t.portfolio_id != p.id:
        raise HTTPException(404, "Trade not found")
    return t


@router.patch("/{pid}/trades/{tid}")
def modify_trade(pid: int, tid: int, body: TradePatch, db: Session = Depends(get_db), user: User = Depends(require("portfolio:write"))):
    t = _trade(db, _own(db, pid, user), tid)
    if t.status in ("closed", "cancelled"):
        raise HTTPException(409, "Trade is no longer active")
    d = body.model_dump(exclude_none=True)
    ref = t.filled_price or t.limit_price  # market orders still pending have no reference yet
    s = 1 if t.direction == "LONG" else -1
    stop, t1, t2 = d.get("stop", t.stop), d.get("target1", t.target1), d.get("target2", t.target2)
    if ref is not None:
        if stop is not None and s * (ref - stop) <= 0 and not (t.state or {}).get("t1_done"):
            raise HTTPException(422, "stop must be on the losing side of the entry")
        for tgt in (t1, t2):
            if tgt is not None and s * (tgt - ref) <= 0:
                raise HTTPException(422, "targets must be on the winning side of the entry")
    if t1 is not None and t2 is not None and s * (t2 - t1) <= 0:
        raise HTTPException(422, "target2 must be beyond target1")
    for k, v in d.items():
        setattr(t, k, v)
    db.commit()
    return {"trade_id": t.id, "status": t.status, "stop": t.stop, "target1": t.target1, "target2": t.target2}


@router.post("/{pid}/trades/{tid}/close")
def close_trade(pid: int, tid: int, db: Session = Depends(get_db), user: User = Depends(require("portfolio:write"))):
    p = _own(db, pid, user)
    t = _trade(db, p, tid)
    if t.status not in ("open", "pending"):
        raise HTTPException(409, "Trade is not open")
    if p.kind == "journal":
        raise HTTPException(422, "Record the exit of a journal trade with the journal endpoint")
    ref = portfolio_service.latest_bar_time(db, db.get(Instrument, t.instrument_id))
    t.close_requested_at = ref.to_pydatetime()
    db.commit()
    portfolio_service.process(db, [t.id])
    t = db.get(PortfolioTrade, t.id)
    return {"trade_id": t.id, "status": t.status,
            "note": "Pending orders are cancelled immediately; open positions close at the next bar's open." if t.status != "closed" else "Closed."}
