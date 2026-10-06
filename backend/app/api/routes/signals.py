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


def _result(oc: SignalOutcome) -> str:
    """open | target2 | target1 | stop | time | not_filled — what happened to a published idea."""
    if oc.status == "open":
        return "open"
    if oc.status == "skipped":
        return "not_filled"
    if oc.t2_hit:
        return "target2"
    if oc.t1_hit:
        return "target1"
    return "stop" if oc.stop_hit else "time"


def _idea_row(sig: Signal, oc: SignalOutcome) -> dict:
    p, opt = sig.payload or {}, sig.market == "NFO"
    u = p.get("underlying") or {}
    pr = p.get("probability") or {}
    row = {"id": sig.id, "market": sig.market, "as_of": str(sig.as_of), "label": sig.symbol, "direction": sig.direction,
           "strategy": ((u if opt else p).get("strategy") or {}).get("name", sig.strategy_key),
           "currency": "INR" if opt else p.get("currency"), "entry_zone": [p.get("entry")] * 2 if opt else p.get("entry_zone"),
           "stop": p.get("stop"), "targets": p.get("targets"), "chance_t1": pr.get("t1_hit_rate"), "sample_size": pr.get("sample_size") or 0,
           "result": _result(oc), "exit_reason": oc.exit_reason, "net_return_pct": oc.net_return_pct,
           "resolved_at": oc.resolved_at.isoformat() if oc.resolved_at else None}
    if opt:  # judged on the NIFTY levels that triggered it; its return is the index move, not the option premium
        row["judged_on"] = {"symbol": u.get("symbol"), "entry_zone": u.get("entry_zone"), "stop": u.get("stop"), "targets": u.get("targets")}
    return row


@router.get("/history")
def history(market: Optional[str] = Query(None, pattern="^(NSE|CRYPTO|NFO)$"), limit: int = Query(300, ge=1, le=1000),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every published idea (VALID setup) and what happened next: target hit, stop hit, time exit, not filled or still open.
    Forward-tracked with the same rules as the backtest. Ideas from SAMPLE data are left out once a market uses real data."""
    from app.services.scan_service import provider_is_sample

    perms = permissions_of(user)
    if "signals:read" not in perms:
        raise HTTPException(403, "Not allowed")
    markets = [m for m in ([market] if market else ["NFO", "NSE", "CRYPTO"]) if m != "NFO" or "options:signals" in perms]
    rows = db.execute(select(Signal, SignalOutcome).join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
                      .where(Signal.market.in_(markets)).order_by(Signal.as_of.desc(), Signal.score.desc()).limit(limit)).all()
    sample = {m: provider_is_sample("NFO" if m == "NFO" else m) for m in markets}
    items = [_idea_row(sig, oc) for sig, oc in rows if bool(sig.is_sample_data) == sample[sig.market]]
    done = [i for i in items if i["result"] in ("target1", "target2", "stop", "time")]
    rets = [i["net_return_pct"] for i in done if i["net_return_pct"] is not None]
    chances = [i["chance_t1"] for i in done if i["chance_t1"] is not None]

    def pct(n: int) -> Optional[float]:
        return round(100 * n / len(done), 1) if done else None

    summary = {"ideas": len(items), "open": sum(i["result"] == "open" for i in items), "not_filled": sum(i["result"] == "not_filled" for i in items),
               "closed": len(done), "target1_or_better": sum(i["result"] in ("target1", "target2") for i in done),
               "target2": sum(i["result"] == "target2" for i in done), "stop": sum(i["result"] == "stop" for i in done),
               "time": sum(i["result"] == "time" for i in done),
               "target1_rate": pct(sum(i["result"] in ("target1", "target2") for i in done)), "stop_rate": pct(sum(i["result"] == "stop" for i in done)),
               "expected_target1_rate": round(sum(chances) / len(chances), 1) if chances else None,
               "avg_net_return_pct": round(sum(rets) / len(rets), 2) if rets else None}
    return {"items": items, "summary": summary,
            "note": ("Each published idea is followed from the next session's open with the backtest's rules: stop assumed first when a bar "
                     "touches both, breakeven after Target 1, closed at the strategy's maximum holding period. NIFTY option ideas are judged "
                     "on the NIFTY levels that triggered them; their return is the index move, not the option premium.")}


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
