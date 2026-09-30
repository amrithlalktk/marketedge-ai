from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import permissions_of, require
from app.core.db import get_db
from app.models import Instrument, Signal, User, Watchlist, WatchlistItem
from app.schemas import WatchlistIn, WatchlistItemIn, WatchlistItemPatch
from app.services.market_data import load_bars
from app.services.scan_service import latest_run
from engine.levels import price_round

router = APIRouter(prefix="/watchlists", tags=["watchlists"])
STANDARD_MAX = 3
MAX_ITEMS = 200


def _own(db: Session, wid: int, user: User) -> Watchlist:
    wl = db.get(Watchlist, wid)
    if wl is None or wl.user_id != user.id:
        raise HTTPException(404, "Watchlist not found")
    return wl


def _out(db: Session, wl: Watchlist) -> dict:
    ids = {it.instrument_id: it.instrument.symbol for it in wl.items}
    bars = load_bars(db, ids) if ids else {}
    active = {}
    for m in {it.instrument.market or "NSE" for it in wl.items}:
        run = latest_run(db, m)
        if run is None:
            continue
        for s in db.scalars(select(Signal).where(Signal.scan_run_id == run.id, Signal.symbol.in_(list(ids.values())))):
            active.setdefault(s.symbol, []).append({"id": s.id, "status": s.status, "strategy": s.strategy_key, "score": s.score, "direction": s.direction})
    items = []
    for it in wl.items:
        df = bars.get(it.instrument.symbol)
        quote = None
        if df is not None and len(df) > 1:
            last = float(df["close"].iloc[-1])
            quote = {"price": price_round(last, last), "change_1d_pct": round(100 * float(last / df["close"].iloc[-2] - 1), 2),
                     "as_of": str(df.index[-1].date())}
        items.append({"id": it.id, "symbol": it.instrument.symbol, "name": it.instrument.name, "tags": it.tags, "note": it.note,
                      "market": it.instrument.market, "currency": it.instrument.currency, "exchange": it.instrument.exchange,
                      "added_at": it.added_at.isoformat(), "quote": quote, "is_sample": it.instrument.is_sample, "setups": active.get(it.instrument.symbol, [])})
    return {"id": wl.id, "name": wl.name, "market": wl.market, "created_at": wl.created_at.isoformat(), "items": items}


@router.get("")
def list_watchlists(db: Session = Depends(get_db), user: User = Depends(require("watchlists:write"))):
    return {"items": [_out(db, w) for w in db.scalars(select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.id))]}


@router.post("", status_code=201)
def create_watchlist(body: WatchlistIn, db: Session = Depends(get_db), user: User = Depends(require("watchlists:write"))):
    count = db.scalar(select(func.count()).select_from(Watchlist).where(Watchlist.user_id == user.id))
    if "watchlists:unlimited" not in permissions_of(user) and count >= STANDARD_MAX:
        raise HTTPException(403, f"Standard accounts can have up to {STANDARD_MAX} watchlists")
    if db.scalar(select(Watchlist).where(Watchlist.user_id == user.id, Watchlist.name == body.name)):
        raise HTTPException(409, "A watchlist with this name already exists")
    wl = Watchlist(user_id=user.id, name=body.name, market=body.market)
    db.add(wl)
    db.commit()
    return _out(db, wl)


@router.delete("/{wid}", status_code=204)
def delete_watchlist(wid: int, db: Session = Depends(get_db), user: User = Depends(require("watchlists:write"))):
    db.delete(_own(db, wid, user))
    db.commit()


@router.post("/{wid}/items", status_code=201)
def add_item(wid: int, body: WatchlistItemIn, db: Session = Depends(get_db), user: User = Depends(require("watchlists:write"))):
    wl = _own(db, wid, user)
    if len(wl.items) >= MAX_ITEMS:
        raise HTTPException(403, f"A watchlist can hold up to {MAX_ITEMS} instruments")
    ins = db.scalar(select(Instrument).where(Instrument.symbol == body.symbol.upper()))
    if ins is None:
        raise HTTPException(404, f"Unknown symbol {body.symbol}")
    if any(it.instrument_id == ins.id for it in wl.items):
        raise HTTPException(409, "Already in watchlist")
    db.add(WatchlistItem(watchlist_id=wl.id, instrument_id=ins.id, tags=body.tags, note=body.note))
    db.commit()
    db.refresh(wl)
    return _out(db, wl)


@router.patch("/{wid}/items/{item_id}")
def patch_item(wid: int, item_id: int, body: WatchlistItemPatch, db: Session = Depends(get_db), user: User = Depends(require("watchlists:write"))):
    wl = _own(db, wid, user)
    it = next((i for i in wl.items if i.id == item_id), None)
    if it is None:
        raise HTTPException(404, "Item not found")
    if body.tags is not None:
        it.tags = [t.strip()[:32] for t in body.tags if t.strip()][:10]
    if body.note is not None:
        it.note = body.note
    db.commit()
    return _out(db, wl)


@router.delete("/{wid}/items/{item_id}", status_code=204)
def delete_item(wid: int, item_id: int, db: Session = Depends(get_db), user: User = Depends(require("watchlists:write"))):
    wl = _own(db, wid, user)
    it = next((i for i in wl.items if i.id == item_id), None)
    if it is None:
        raise HTTPException(404, "Item not found")
    db.delete(it)
    db.commit()
