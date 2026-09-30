from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.api.routes.markets import latest_snapshot
from app.core.cache import get_cache
from app.core.db import get_db
from app.models import MLModel, User
from app.services import ml_service

router = APIRouter(prefix="/ml", tags=["machine learning"])
MARKET_Q = Query(None, pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$")
NOTE = ("ML probabilities are calibrated estimates validated with purged walk-forward tests against the platform's empirical hit rate. "
        "A model can only be activated if it beats that baseline out of sample; it is always shown next to the empirical rate, never instead of it.")


def _summary(m: MLModel) -> dict:
    oos = (m.metrics.get("results", {}).get(m.algo) or {}).get("oos") or {}
    return {"id": m.id, "market": m.market, "target": m.target, "algo": m.algo, "version": m.version, "status": m.status, "eligible": m.eligible,
            "gate": m.metrics.get("gate"), "oos_model": oos.get("model"), "oos_baseline": oos.get("baseline"), "n_events": m.metrics.get("n_events"),
            "period": m.metrics.get("period"), "is_sample_data": m.is_sample_data, "created_at": m.created_at.isoformat(),
            "activated_at": m.activated_at.isoformat() if m.activated_at else None, "engine_version": m.engine_version}


@router.get("/models", dependencies=[Depends(require("signals:read_all"))])
def list_models(db: Session = Depends(get_db), market: Optional[str] = MARKET_Q):
    q = select(MLModel).order_by(MLModel.id.desc()).limit(50)
    if market:
        q = q.where(MLModel.market == market)
    return {"items": [_summary(m) for m in db.scalars(q)], "note": NOTE}


@router.get("/models/{model_id}", dependencies=[Depends(require("signals:read_all"))])
def get_model(model_id: int, db: Session = Depends(get_db)):
    m = db.get(MLModel, model_id)
    if m is None:
        raise HTTPException(404, "Model not found")
    return {**_summary(m), "results": m.metrics.get("results"), "importance": m.metrics.get("importance"), "config": m.metrics.get("config"),
            "features": m.features, "calibrated": bool((m.artifact or {}).get("isotonic")), "note": NOTE}


class StatusIn(BaseModel):
    status: str = Field(pattern="^(active|retired|candidate)$")
    reason: Optional[str] = Field(default=None, max_length=300)


@router.patch("/models/{model_id}")
def set_status(model_id: int, body: StatusIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin:settings"))):
    m = db.get(MLModel, model_id)
    if m is None:
        raise HTTPException(404, "Model not found")
    try:
        ml_service.set_status(db, m, body.status, user.id, body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    get_cache().invalidate_prefix("me:")
    return {**_summary(m), "note": "Takes effect from the next scan of this market."}


@router.get("/regime", dependencies=[Depends(require("market:read"))])
def regime(db: Session = Depends(get_db), market: Optional[str] = MARKET_Q):
    ov = latest_snapshot(db, "overview", market)
    if not ov or not ov.get("regime_ml"):
        raise HTTPException(404, "No ML regime computed yet (runs with each scan)")
    return {**ov["regime_ml"], "market": ov.get("market")}
