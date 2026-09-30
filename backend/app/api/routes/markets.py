from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.db import get_db
from app.models import MarketRegime, MarketSnapshot
from engine.analyzer import DISCLAIMER

router = APIRouter(prefix="/markets", tags=["markets"])


def latest_snapshot(db: Session, kind: str, market: Optional[str] = None) -> Optional[dict]:
    market = market or get_settings().market
    key = f"me:snap:{market}:{kind}"
    cached = get_cache().get_json(key)
    if cached is not None:
        return cached
    row = db.scalar(select(MarketSnapshot).where(MarketSnapshot.market == market, MarketSnapshot.kind == kind).order_by(MarketSnapshot.id.desc()).limit(1))
    if row is None:
        return None
    out = {**row.payload, "snapshot_created_at": row.created_at.isoformat(), "scan_run_id": row.scan_run_id}
    get_cache().set_json(key, out, ttl=600)
    return out


def _need(snap, what):
    if snap is None:
        raise HTTPException(404, f"No {what} computed yet. An administrator must run data ingestion and a scan.")
    return snap


MARKET_Q = Query(None, pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$")


@router.get("", dependencies=[Depends(require("market:read"))])
def list_markets(db: Session = Depends(get_db)):
    from app.core.markets import enabled_markets, market_config
    from app.services.scan_service import latest_run

    out = []
    for m in enabled_markets():
        mc = market_config(m)
        run = latest_run(db, m)
        out.append({"id": m, "name": mc.profile.name, "group": mc.profile.group, "asset_class": mc.profile.asset_class,
                    "calendar": mc.profile.calendar, "benchmark": mc.benchmark, "provider": mc.provider,
                    "last_scan": {"as_of": str(run.as_of), "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                                  "valid": (run.stats or {}).get("valid"), "is_sample": (run.stats or {}).get("is_sample")} if run else None,
                    "sample_provider": mc.provider == "sample"})
    from app.core.config import get_settings

    ids = [m["id"] for m in out]
    pref = get_settings().default_market
    default = pref if pref in ids else next((m["id"] for m in out if not m["sample_provider"]), ids[0] if ids else "NSE")
    return {"items": out, "default_market": default}


@router.get("/global-overview", dependencies=[Depends(require("market:read"))])
def global_overview(db: Session = Depends(get_db)):
    """One dashboard across every enabled market (latest snapshot of each)."""
    from app.core.markets import enabled_markets

    markets = {}
    for m in enabled_markets():
        ov = latest_snapshot(db, "overview", m)
        if ov:
            markets[m] = {"cards": ov.get("indices", []), "regime": ov.get("regime"), "is_sample": ov.get("is_sample"),
                          "profile": ov.get("profile"), "scan": latest_snapshot(db, "scan_summary", m),
                          **({k: ov.get(k) for k in ("btc_dominance_pct", "btc_dominance_basis", "stablecoin_flows")} if m == "CRYPTO" else {})}
    if not markets:
        raise HTTPException(404, "No market scans computed yet.")
    return {"markets": markets, "disclaimer": DISCLAIMER}


@router.get("/overview", dependencies=[Depends(require("market:read"))])
def overview(db: Session = Depends(get_db), market: Optional[str] = MARKET_Q):
    ov = _need(latest_snapshot(db, "overview", market), "market overview")
    return {**ov, "breadth": latest_snapshot(db, "breadth", market), "scan": latest_snapshot(db, "scan_summary", market),
            "disclaimer": DISCLAIMER}


@router.get("/regime", dependencies=[Depends(require("market:read"))])
def regime(db: Session = Depends(get_db), history: int = 90, market: Optional[str] = MARKET_Q):
    market = market or get_settings().market
    rows = list(db.scalars(select(MarketRegime).where(MarketRegime.market == market).order_by(MarketRegime.as_of.desc()).limit(min(history, 500))))
    if not rows:
        raise HTTPException(404, "No regime computed yet")
    return {"current": rows[0].payload, "history": [{"as_of": str(r.as_of), "regime": r.regime, "volatility": r.volatility} for r in rows]}


@router.get("/breadth", dependencies=[Depends(require("market:read"))])
def breadth(db: Session = Depends(get_db), market: Optional[str] = MARKET_Q):
    return _need(latest_snapshot(db, "breadth", market), "breadth")


@router.get("/sectors", dependencies=[Depends(require("market:read"))])
def sectors(db: Session = Depends(get_db), market: Optional[str] = MARKET_Q):
    return _need(latest_snapshot(db, "sectors", market), "sector rotation")
