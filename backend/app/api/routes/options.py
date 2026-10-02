from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import permissions_of, require
from app.core.cache import get_cache
from app.core.db import get_db
from app.models import MarketSnapshot, User
from app.services.options_service import MARKET, enriched_latest_chain, evaluate_legs
from engine.options.analyzer import OPTIONS_DISCLAIMER

router = APIRouter(prefix="/options", tags=["options"])


def _snapshot(db: Session) -> dict:
    key = "me:opt:snapshot"
    cached = get_cache().get_json(key)
    if cached is not None:
        return cached
    from app.services.scan_service import matches_provider, snapshot_is_sample

    rows = db.scalars(select(MarketSnapshot).where(MarketSnapshot.market == MARKET, MarketSnapshot.kind == "options").order_by(MarketSnapshot.id.desc()).limit(25))
    row = next((r for r in rows if matches_provider(MARKET, snapshot_is_sample(db, r))), None)
    if row is None:
        raise HTTPException(404, "No options analysis on the current data source yet. It runs after the daily NSE scan (18:30 IST).")
    out = {**row.payload, "snapshot_created_at": row.created_at.isoformat(), "scan_run_id": row.scan_run_id}
    get_cache().set_json(key, out, ttl=600)
    return out


@router.get("/nifty", dependencies=[Depends(require("options:read"))])
def nifty(db: Session = Depends(get_db)):
    """Market state, IV, expected move, OI analytics, futures basis. Setups/strategies are summarised by count."""
    s = _snapshot(db)
    return {k: v for k, v in s.items() if k not in ("chain", "option_setups", "strategies", "underlying_setups")} | {
        "counts": {"option_setups": len(s["option_setups"]), "valid": sum(x["status"] == "VALID" for x in s["option_setups"]),
                   "strategies_proposed": len(s["strategies"]["proposed"])}}


@router.get("/nifty/expiries", dependencies=[Depends(require("options:read"))])
def expiries(db: Session = Depends(get_db)):
    s = _snapshot(db)
    return {"expiries": s["expiries"], "as_of": s["underlying"]["as_of"]}


@router.get("/nifty/chain", dependencies=[Depends(require("options:read"))])
def chain(db: Session = Depends(get_db), expiry: Optional[str] = None, width: int = Query(20, ge=5, le=80)):
    s = _snapshot(db)
    exp = expiry or s["oi"]["expiry"]  # nearest tradable expiry (skips one settling today)
    rows = s["chain"].get(exp)
    if rows is None:  # expiries beyond the precomputed first three: compute from stored chain
        snap = enriched_latest_chain(db)
        if snap is None or exp not in s["expiries"]:
            raise HTTPException(404, "Unknown expiry")
        c = snap["chain"]
        c = c[c["expiry"].astype(str) == exp]
        rows = []
        for k in sorted(c["strike"].unique()):
            r = {"strike": float(k)}
            for t in ("CE", "PE"):
                x = c[(c["strike"] == k) & (c["option_type"] == t)]
                if len(x):
                    x = x.iloc[0]
                    r[t] = {kk: (None if x[kk] != x[kk] else round(float(x[kk]), 4)) for kk in ("bid", "ask", "ltp", "mid", "spread_pct", "volume", "oi", "oi_change", "delta", "gamma", "theta", "vega")}
                    r[t]["iv"] = None if x["iv"] != x["iv"] else round(100 * float(x["iv"]), 2)
                    r[t]["liquid"] = bool(x["liquid"])
            rows.append(r)
    spot = s["underlying"]["spot"]
    atm_i = min(range(len(rows)), key=lambda i: abs(rows[i]["strike"] - spot)) if rows else 0
    rows = rows[max(0, atm_i - width): atm_i + width + 1]
    return {"expiry": exp, "spot": spot, "atm_strike": rows[min(width, atm_i)]["strike"] if rows else None, "rows": rows,
            "lot_size": s["underlying"]["lot_size"], "as_of": s["underlying"]["as_of"], "data": s["data"]}


@router.get("/signals")
def option_signals(db: Session = Depends(get_db), user: User = Depends(require("options:signals"))):
    s = _snapshot(db)
    setups = s["option_setups"]
    if "signals:read_all" not in permissions_of(user):
        setups = [x for x in setups if x["status"] == "VALID"]
    return {"as_of": s["underlying"]["as_of"], "status": s["status"], "market_message": s["market_message"], "market_state": s["market_state"],
            "items": setups, "underlying_setups": [{k: u[k] for k in ("strategy", "direction", "status", "score", "current_price", "stop", "targets", "rr_t2", "probability", "checks")}
                                                   for u in s["underlying_setups"]], "data": s["data"], "disclaimer": OPTIONS_DISCLAIMER}


@router.get("/strategies", dependencies=[Depends(require("options:signals"))])
def strategies(db: Session = Depends(get_db)):
    s = _snapshot(db)
    return {"as_of": s["underlying"]["as_of"], "market_state": s["market_state"], "iv": s["iv"], **s["strategies"], "data": s["data"],
            "disclaimer": OPTIONS_DISCLAIMER}


class LegIn(BaseModel):
    strike: float = Field(gt=0)
    option_type: str = Field(pattern="^(CE|PE)$")
    expiry: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    side: str = Field(pattern="^(BUY|SELL)$")
    lots: int = Field(default=1, ge=1, le=100)


class PayoffIn(BaseModel):
    legs: List[LegIn] = Field(min_length=1, max_length=6)


@router.post("/payoff", dependencies=[Depends(require("options:read"))])
def payoff(body: PayoffIn, db: Session = Depends(get_db)):
    try:
        out = evaluate_legs(db, [lg.model_dump() for lg in body.legs])
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**out, "disclaimer": OPTIONS_DISCLAIMER}
