from __future__ import annotations

import hmac
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Notification, User
from app.notify.channels import CHANNELS, channel_status
from app.services import notify_service, telegram_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


class SettingsIn(BaseModel):
    email_enabled: Optional[bool] = None
    default_channels: Optional[List[str]] = None
    whatsapp_number: Optional[str] = Field(default=None, max_length=20)
    whatsapp_opt_in: Optional[bool] = None
    quiet_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    quiet_end_hour: Optional[int] = Field(default=None, ge=0, le=23)

    @field_validator("whatsapp_number")
    @classmethod
    def _e164(cls, v):
        if v in (None, ""):
            return v
        if not re.fullmatch(r"\+[1-9]\d{7,14}", v):
            raise ValueError("WhatsApp number must be in international E.164 format, e.g. +919876543210")
        return v


class PushSubIn(BaseModel):
    endpoint: str = Field(max_length=1000, pattern=r"^https://")
    keys: dict


def _out(n: Notification) -> dict:
    return {"id": n.id, "title": n.title, "body": n.body, "link": n.link, "payload": n.payload, "deliveries": n.deliveries,
            "read": n.read_at is not None, "created_at": n.created_at.isoformat()}


@router.get("")
def list_notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user), unread: bool = False, limit: int = Query(50, ge=1, le=200)):
    q = select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc()).limit(limit)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    count = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None)))
    return {"items": [_out(n) for n in db.scalars(q)], "unread": count}


@router.post("/read")
def mark_read(ids: Optional[List[int]] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from datetime import datetime, timezone

    q = update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if ids:
        q = q.where(Notification.id.in_(ids))
    db.execute(q.values(read_at=datetime.now(timezone.utc)))
    db.commit()
    return {"ok": True}


@router.get("/settings")
def get_settings_(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = notify_service.settings_for(db, user.id)
    db.commit()
    return {"email_enabled": s.email_enabled, "email_address": user.email, "telegram_linked": bool(s.telegram_chat_id),
            "whatsapp_number": s.whatsapp_number, "push_subscriptions": len(s.push_subscriptions or []), "default_channels": s.default_channels,
            "quiet_start_hour": s.quiet_start_hour, "quiet_end_hour": s.quiet_end_hour, "channels": channel_status(),
            "vapid_public_key": get_settings().vapid_public_key}


@router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = notify_service.settings_for(db, user.id)
    d = body.model_dump(exclude_none=True)
    if "default_channels" in d:
        bad = [c for c in d["default_channels"] if c not in CHANNELS]
        if bad:
            raise HTTPException(422, f"Unknown channel(s): {bad}")
        s.default_channels = list(dict.fromkeys(["web"] + d["default_channels"]))
    if "email_enabled" in d:
        s.email_enabled = d["email_enabled"]
    if "whatsapp_number" in d:
        if d["whatsapp_number"] and not d.get("whatsapp_opt_in"):
            raise HTTPException(422, "WhatsApp messages require your explicit opt-in (whatsapp_opt_in: true)")
        s.whatsapp_number = d["whatsapp_number"] or None
    for k in ("quiet_start_hour", "quiet_end_hour"):
        if k in d:
            setattr(s, k, d[k])
    db.commit()
    return get_settings_(db, user)


@router.post("/test")
def test_notification(db: Session = Depends(get_db), user: User = Depends(require("alerts:write"))):
    n = notify_service.notify(db, user.id, "Test notification", "If you can read this, this channel works.", link="/account")
    return _out(db.get(Notification, n.id))


@router.post("/telegram/link")
def telegram_link(db: Session = Depends(get_db), user: User = Depends(require("alerts:write"))):
    return telegram_service.new_link_code(db, user.id)


@router.delete("/telegram/link", status_code=204)
def telegram_unlink(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = notify_service.settings_for(db, user.id)
    s.telegram_chat_id = None
    s.default_channels = [c for c in (s.default_channels or []) if c != "telegram"] or ["web"]
    db.commit()


@router.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(request: Request, db: Session = Depends(get_db),
                           x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)):
    secret = get_settings().telegram_webhook_secret
    if not secret or not x_telegram_bot_api_secret_token or not hmac.compare_digest(secret, x_telegram_bot_api_secret_token):
        raise HTTPException(403, "Forbidden")
    telegram_service.handle_update(db, await request.json())
    return {"ok": True}


@router.post("/push/subscribe", status_code=201)
def push_subscribe(body: PushSubIn, db: Session = Depends(get_db), user: User = Depends(require("alerts:write"))):
    s = notify_service.settings_for(db, user.id)
    subs = [x for x in (s.push_subscriptions or []) if x.get("endpoint") != body.endpoint]
    s.push_subscriptions = (subs + [body.model_dump()])[-10:]
    if "push" not in (s.default_channels or []):
        s.default_channels = list(s.default_channels or ["web"]) + ["push"]
    db.commit()
    return {"subscriptions": len(s.push_subscriptions)}


@router.delete("/push/subscribe", status_code=204)
def push_unsubscribe(endpoint: str = Query(max_length=1000), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = notify_service.settings_for(db, user.id)
    s.push_subscriptions = [x for x in (s.push_subscriptions or []) if x.get("endpoint") != endpoint]
    db.commit()
