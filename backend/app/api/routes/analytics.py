from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.db import get_db
from app.services.market_data import instrument_maps
from app.services.scan_service import latest_events, latest_run
from engine.probability import GROUPINGS, explore

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/hit-rates", dependencies=[Depends(require("signals:read_all"))])
def hit_rates(db: Session = Depends(get_db), strategy: Optional[str] = Query(None, max_length=64), regime: Optional[str] = Query(None, max_length=32),
              score_bucket: Optional[str] = Query(None, pattern=r"^(75\+|60-75|<60)$"), sector: Optional[str] = Query(None, max_length=64),
              symbol: Optional[str] = Query(None, max_length=64), start: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
              end: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), group_by: Optional[str] = Query(None, pattern="^(" + "|".join(GROUPINGS) + ")$"),
              min_group: int = Query(5, ge=1, le=1000), market: Optional[str] = Query(None, pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$")):
    """Explore the historical events behind every published probability (latest scan's event set)."""
    market = market or get_settings().market
    run = latest_run(db, market)
    if run is None:
        raise HTTPException(404, "No scan has completed yet")
    key = f"me:hitrates:{market}:{run.id}:{strategy}:{regime}:{score_bucket}:{sector}:{symbol}:{start}:{end}:{group_by}:{min_group}"
    cached = get_cache().get_json(key)
    if cached is not None:
        return cached
    _, info = instrument_maps(db, market)
    out = explore(latest_events(db, market), strategy_id=strategy, regime=regime, score_bucket_=score_bucket, sector=sector,
                  symbol=symbol.upper() if symbol else None, start=start, end=end, group_by=group_by,
                  sectors={k: v["sector"] for k, v in info.items() if v["sector"]}, min_group=min_group)
    out.update({"scan_run_id": run.id, "as_of": str(run.as_of), "groupings": list(GROUPINGS),
                "definitions": {"t1_hit_rate": "T1 touched before the initial stop", "stop_rate": "initial stop before T1",
                                "mfe_r": "maximum favourable excursion in R", "mae_r": "maximum adverse excursion in R"}})
    get_cache().set_json(key, out, ttl=1800)
    return out
