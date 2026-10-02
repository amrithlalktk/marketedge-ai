from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, permissions_of, require
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Signal, SignalOutcome, User
from app.services.scan_service import latest_run
from engine.analyzer import DISCLAIMER

router = APIRouter(prefix="/signals", tags=["signals"])
STANDARD_TOP_LIMIT = 5

SORTS = {
    "score": lambda s: -s["score"],
    "rr": lambda s: -s["rr_t2"],
    "hit_rate": lambda s: -(s["probability"].get("t1_hit_rate") or 0),
    "volume": lambda s: -(s["components"].get("volume") or 0),
    "momentum": lambda s: -(s["components"].get("momentum") or 0),
}


def _summary(p: dict, signal_id: int) -> dict:
    pr = p.get("probability", {})
    extra = {k: p[k] for k in ("inr", "pips", "market_cap_usd", "spread_bps", "exchange", "events", "ml") if k in p}
    if "news" in p:
        extra["news"] = {k: p["news"].get(k) for k in ("window_days", "articles", "counts", "net_score", "latest", "note", "checks")}
    if "derivatives" in p:
        d = p["derivatives"]
        extra["derivatives"] = {k: d.get(k) for k in ("available", "funding", "open_interest", "long_short_ratio", "note")}
    return {**extra,
        "id": signal_id, "symbol": p["symbol"], "name": p.get("name"), "sector": p.get("sector"), "market": p["market"],
        "currency": p.get("currency"), "direction": p["direction"], "status": p["status"], "as_of": p["as_of"],
        "setup_type": p["setup_type"], "strategy_id": p["strategy"]["id"], "strategy_name": p["strategy"]["name"],
        "current_price": p["current_price"], "entry_zone": p["entry_zone"], "stop": p["stop"], "targets": p["targets"],
        "risk_pct": p["risk_pct"], "reward_pct_t2": p["reward_pct_t2"], "rr_t1": p["rr_t1"], "rr_t2": p["rr_t2"], "score": p["score"],
        "score_label": p["score_label"], "components": p["components"],
        "probability": {k: pr.get(k) for k in ("t1_hit_rate", "t2_hit_rate", "stop_rate", "sample_size", "backtest_period", "conditioning", "t1_ci95")},
        "expected_holding_days": p.get("expected_holding_days"), "reasons": p["explanation"]["agreeing"][:6],
        "risk_factors": p["explanation"]["risk_factors"][:4],
        "blocking_checks": [c["name"] + ": " + c["detail"] for c in p["checks"] if not c["passed"] and c["severity"] == "block"],
        "data": p.get("data", {}),
    }


def _load(db: Session, status: str, market: str):
    run = latest_run(db, market)
    if run is None:
        return None, []
    rows = db.scalars(select(Signal).where(Signal.scan_run_id == run.id, Signal.status == status, Signal.market == market)).all()
    return run, rows


MARKET_Q = Query(None, pattern="^(NSE|CRYPTO)$")
SORT_NOTE = "Sorting orders current setups by a measured attribute; it is not a ranking of future performance."


def _top(db: Session, user: User, markets, limit: int, sort: str, direction: Optional[str]) -> dict:
    items, runs = [], {}
    for m in markets:
        run, rows = _load(db, "VALID", m)
        if run is None:
            continue
        runs[m] = {"as_of": str(run.as_of), "scan_run_id": run.id, "market_message": (run.stats or {}).get("market_message")}
        items.extend(_summary(r.payload, r.id) for r in rows if not direction or r.direction == direction)
    if not runs:
        raise HTTPException(404, "No scan has completed yet for " + ", ".join(markets))
    if "signals:read_all" not in permissions_of(user):
        limit = min(limit, STANDARD_TOP_LIMIT)
    items.sort(key=SORTS[sort])
    first = next(iter(runs.values()))
    return {"as_of": first["as_of"], "scan_run_id": first["scan_run_id"], "market": markets[0] if len(markets) == 1 else "+".join(markets),
            "markets": runs, "sort": sort, "items": items[:limit], "total_valid": len(items),
            "market_message": first["market_message"] if len(markets) == 1 else (None if items else "NO VALID SETUP in any of these markets today."),
            "sort_note": SORT_NOTE, "disclaimer": DISCLAIMER}


@router.get("/top")
def top(db: Session = Depends(get_db), user: User = Depends(require("signals:read")), limit: int = Query(10, ge=1, le=100),
        sort: str = Query("score", pattern="^(score|rr|hit_rate|volume|momentum)$"), direction: Optional[str] = Query(None, pattern="^(LONG|SHORT)$"),
        market: Optional[str] = MARKET_Q):
    return _top(db, user, [market or get_settings().market], limit, sort, direction)


@router.get("")
def list_signals(db: Session = Depends(get_db), user: User = Depends(require("signals:read_all")),
                 status: str = Query("VALID", pattern="^(VALID|NO_TRADE)$"), strategy: Optional[str] = None,
                 direction: Optional[str] = Query(None, pattern="^(LONG|SHORT)$"), min_score: float = Query(0, ge=0, le=100),
                 sort: str = Query("score", pattern="^(score|rr|hit_rate|volume|momentum)$"), market: Optional[str] = MARKET_Q):
    market = market or get_settings().market
    run, rows = _load(db, status, market)
    if run is None:
        raise HTTPException(404, "No scan has completed yet")
    items = [_summary(r.payload, r.id) for r in rows
             if (not strategy or r.strategy_key == strategy) and (not direction or r.direction == direction) and r.score >= min_score]
    items.sort(key=SORTS[sort])
    return {"as_of": str(run.as_of), "status": status, "items": items, "disclaimer": DISCLAIMER}


@router.get("/track-record", dependencies=[Depends(require("signals:read"))])
def track_record(db: Session = Depends(get_db)):
    """Forward-tracked outcomes of previously published VALID signals (not a backtest)."""
    raw = db.execute(select(Signal.market, Signal.symbol, Signal.strategy_key, Signal.as_of, Signal.id, SignalOutcome.status, SignalOutcome.t1_hit,
                            SignalOutcome.stop_hit, SignalOutcome.net_return_pct)
                     .join(SignalOutcome, SignalOutcome.signal_id == Signal.id).order_by(Signal.id)).all()
    first, agg = {}, {}
    for r in raw:  # one outcome per published setup, even if older rescans stored duplicates
        first.setdefault((r[0], r[1], r[2], r[3]), r)
    for r in first.values():
        a = agg.setdefault((r[2], r[5]), {"count": 0, "t1": 0, "stop": 0, "rets": []})
        a["count"] += 1
        a["t1"] += int(bool(r[6]))
        a["stop"] += int(bool(r[7]))
        if r[8] is not None:
            a["rets"].append(r[8])
    return {"items": [{"strategy": k[0], "status": k[1], "count": v["count"], "t1_hits": v["t1"], "stop_hits": v["stop"],
                       "avg_net_return_pct": round(sum(v["rets"]) / len(v["rets"]), 2) if v["rets"] else None} for k, v in sorted(agg.items())],
            "note": "Live forward-tracked outcomes of published setups, resolved with the same rules as the backtest."}


@router.get("/{signal_id}")
def get_signal(signal_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sig = db.get(Signal, signal_id)
    if sig is None or sig.market == "NFO":  # option setups are served by /options/signals
        raise HTTPException(404, "Signal not found")
    perms = permissions_of(user)
    if "signals:read" not in perms:
        raise HTTPException(403, "Missing permission: signals:read")
    if sig.status != "VALID" and "signals:read_all" not in perms:
        raise HTTPException(403, "Missing permission: signals:read_all")
    oc = db.get(SignalOutcome, sig.id)
    return {**sig.payload, "id": sig.id, "outcome": ({"status": oc.status, "t1_hit": oc.t1_hit, "t2_hit": oc.t2_hit, "stop_hit": oc.stop_hit,
                                                       "exit_reason": oc.exit_reason, "net_return_pct": oc.net_return_pct} if oc else None)}



# Per-market convenience endpoints (spec §37)
market_router = APIRouter(tags=["signals"])


@market_router.get("/crypto/signals")
def crypto_signals(db: Session = Depends(get_db), user: User = Depends(require("signals:read")), limit: int = Query(20, ge=1, le=100),
                   sort: str = Query("score", pattern="^(score|rr|hit_rate|volume|momentum)$"), direction: Optional[str] = Query(None, pattern="^(LONG|SHORT)$")):
    return _top(db, user, ["CRYPTO"], limit, sort, direction)
