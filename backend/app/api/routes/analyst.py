from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.db import get_db
from app.models import AnalystQuery, User
from app.services import analyst_service

router = APIRouter(prefix="/analyst", tags=["ai analyst"])


class AskIn(BaseModel):
    question: str = Field(default="Why is this setup appearing?", min_length=3, max_length=1000)
    signal_id: Optional[int] = Field(default=None, ge=1)
    symbol: Optional[str] = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _one(self):
        if not self.signal_id and not self.symbol:
            raise ValueError("Provide signal_id or symbol")
        return self


@router.post("/ask")
def ask(body: AskIn, db: Session = Depends(get_db), user: User = Depends(require("analyst:ask"))):
    limit = get_settings().analyst_per_hour
    if get_cache().incr_window(f"rl:analyst:{user.id}:{int(time.time() // 3600)}", 3700) > limit:
        raise HTTPException(429, f"AI analyst limit reached ({limit} questions per hour)")
    try:
        return analyst_service.ask(db, user.id, body.question, body.signal_id, body.symbol)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/history")
def history(db: Session = Depends(get_db), user: User = Depends(require("analyst:ask")), limit: int = Query(20, ge=1, le=100)):
    rows = db.scalars(select(AnalystQuery).where(AnalystQuery.user_id == user.id).order_by(AnalystQuery.id.desc()).limit(limit))
    return {"items": [{"id": r.id, "question": r.question, "answer": r.answer, "mode": r.mode, "model": r.model, "grounded": r.grounded,
                       "unverified_numbers": r.unverified_numbers, "signal_id": r.signal_id, "symbol": r.symbol,
                       "created_at": r.created_at.isoformat()} for r in rows]}
