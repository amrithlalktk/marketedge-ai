from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import permissions_of, require
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Alert, Instrument, Signal, User
from app.notify.channels import CHANNELS, channel_status
from app.services import alert_service
from engine.alerts import BAR_KINDS, KINDS, OPTIONAL_PARAMS, PARAMS, validate_params

router = APIRouter(prefix="/alerts", tags=["alerts"])
CHANNEL_PATTERN = "^(" + "|".join(CHANNELS) + ")$"


class AlertIn(BaseModel):
    kind: str = Field(max_length=32)
    symbol: Optional[str] = Field(default=None, max_length=64)
    params: dict = Field(default_factory=dict)
    channels: List[str] = Field(default_factory=lambda: ["web"], max_length=5)
    repeat: bool = False
    cooldown_minutes: int = Field(default=1440, ge=5, le=60 * 24 * 30)
    note: str = Field(default="", max_length=300)
    expires_at: Optional[datetime] = None


class AlertPatch(BaseModel):
    status: Optional[str] = Field(default=None, pattern="^(active|paused)$")
    channels: Optional[List[str]] = None
    repeat: Optional[bool] = None
    cooldown_minutes: Optional[int] = Field(default=None, ge=5, le=60 * 24 * 30)
    note: Optional[str] = Field(default=None, max_length=300)


class FromSetupIn(BaseModel):
    kinds: List[str] = Field(default_factory=lambda: ["entry", "target1", "stop"], min_length=1, max_length=4)
    channels: List[str] = Field(default_factory=lambda: ["web"], max_length=5)


def _check_channels(ch: List[str]) -> List[str]:
    bad = [c for c in ch if c not in CHANNELS]
    if bad:
        raise HTTPException(422, f"Unknown channel(s): {bad}")
    return list(dict.fromkeys(ch))


def _limit(db: Session, user: User, adding: int = 1) -> None:
    if "alerts:unlimited" in permissions_of(user):
        return
    n = db.scalar(select(func.count()).select_from(Alert).where(Alert.user_id == user.id, Alert.status.in_(("active", "paused"))))
    if n + adding > get_settings().alerts_max_standard:
        raise HTTPException(403, f"Standard accounts can have {get_settings().alerts_max_standard} active or paused alerts (delete one to add another)")


def _out(a: Alert) -> dict:
    return {"id": a.id, "kind": a.kind, "description": KINDS[a.kind], "symbol": a.symbol, "params": a.params, "channels": a.channels, "status": a.status,
            "repeat": a.repeat, "cooldown_minutes": a.cooldown_minutes, "trigger_count": a.trigger_count,
            "last_triggered_at": a.last_triggered_at.isoformat() if a.last_triggered_at else None, "note": a.note, "signal_id": a.signal_id,
            "expires_at": a.expires_at.isoformat() if a.expires_at else None, "created_at": a.created_at.isoformat()}


@router.get("/kinds", dependencies=[Depends(require("alerts:write"))])
def kinds():
    return {"kinds": [{"kind": k, "description": d, "params": PARAMS[k], "optional_params": OPTIONAL_PARAMS.get(k, []), "needs_symbol": k in BAR_KINDS}
                      for k, d in KINDS.items()],
            "channels": channel_status(),
            "evaluation": "Evaluated after each scan and every 5 minutes on the latest stored bars (end-of-day unless an intraday feed is configured)."}


@router.get("")
def list_alerts(db: Session = Depends(get_db), user: User = Depends(require("alerts:write"))):
    return {"items": [_out(a) for a in db.scalars(select(Alert).where(Alert.user_id == user.id).order_by(Alert.id.desc()))]}


@router.post("", status_code=201)
def create_alert(body: AlertIn, db: Session = Depends(get_db), user: User = Depends(require("alerts:write"))):
    _limit(db, user)
    try:
        params = validate_params(body.kind, body.params)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    ins = None
    if body.kind in BAR_KINDS:
        if not body.symbol:
            raise HTTPException(422, f"{body.kind} alerts need a symbol")
        ins = db.scalar(select(Instrument).where(Instrument.symbol == body.symbol.upper()))
        if ins is None:
            raise HTTPException(404, f"Unknown symbol {body.symbol}")
    a = Alert(user_id=user.id, instrument_id=ins.id if ins else None, symbol=ins.symbol if ins else None, kind=body.kind, params=params,
              channels=_check_channels(body.channels), repeat=body.repeat, cooldown_minutes=body.cooldown_minutes, note=body.note, expires_at=body.expires_at)
    db.add(a)
    db.commit()
    return _out(a)


@router.post("/from-setup/{signal_id}", status_code=201)
def from_setup(signal_id: int, body: FromSetupIn, db: Session = Depends(get_db), user: User = Depends(require("alerts:write"))):
    sig = db.get(Signal, signal_id)
    if sig is None or sig.market == "NFO":
        raise HTTPException(404, "Setup not found")
    _limit(db, user, len(body.kinds))
    try:
        made = alert_service.alerts_from_setup(db, user.id, sig.payload, sig.id, body.kinds, _check_channels(body.channels))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"items": [_out(a) for a in made]}


def _own(db: Session, aid: int, user: User) -> Alert:
    a = db.get(Alert, aid)
    if a is None or a.user_id != user.id:
        raise HTTPException(404, "Alert not found")
    return a


@router.patch("/{aid}")
def patch_alert(aid: int, body: AlertPatch, db: Session = Depends(get_db), user: User = Depends(require("alerts:write"))):
    a = _own(db, aid, user)
    d = body.model_dump(exclude_none=True)
    if "channels" in d:
        d["channels"] = _check_channels(d["channels"])
    if d.get("status") == "active" and a.status not in ("active", "paused"):
        _limit(db, user)
    for k, v in d.items():
        setattr(a, k, v)
    db.commit()
    return _out(a)


@router.delete("/{aid}", status_code=204)
def delete_alert(aid: int, db: Session = Depends(get_db), user: User = Depends(require("alerts:write"))):
    db.delete(_own(db, aid, user))
    db.commit()
