"""Create notifications and deliver them over the user's channels (web always; others when configured + enabled)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import Notification, NotificationSetting, User
from app.notify.channels import CHANNELS

IST = timezone(timedelta(hours=5, minutes=30))


def settings_for(db: Session, user_id: int) -> NotificationSetting:
    s = db.get(NotificationSetting, user_id)
    if s is None:
        s = NotificationSetting(user_id=user_id, default_channels=["web"], push_subscriptions=[])
        db.add(s)
        db.flush()
    return s


def _quiet(s: NotificationSetting, now: datetime) -> bool:
    if s.quiet_start_hour is None or s.quiet_end_hour is None:
        return False
    h = now.astimezone(IST).hour
    a, b = s.quiet_start_hour, s.quiet_end_hour
    return a <= h < b if a < b else (h >= a or h < b)


def notify(db: Session, user_id: int, title: str, body: str, link: Optional[str] = None, payload: Optional[dict] = None,
           channels: Optional[List[str]] = None, alert_id: Optional[int] = None, deliver_now: bool = True) -> Notification:
    s = settings_for(db, user_id)
    chans = list(dict.fromkeys(["web"] + list(channels or s.default_channels or [])))
    n = Notification(user_id=user_id, alert_id=alert_id, title=title[:200], body=body, link=link, payload=payload or {},
                     deliveries={c: {"status": "queued"} for c in chans if c in CHANNELS})
    db.add(n)
    db.commit()
    if deliver_now:
        deliver(db, n.id)
    return n


def deliver(db: Session, notification_id: int, http=None) -> dict:
    n = db.get(Notification, notification_id)
    user = db.get(User, n.user_id)
    s = settings_for(db, n.user_id)
    now = datetime.now(timezone.utc)
    out = dict(n.deliveries or {})
    for name in list(out):
        if out[name].get("status") == "sent":
            continue
        if name != "web" and _quiet(s, now):
            out[name] = {"status": "skipped", "error": "quiet hours", "at": now.isoformat()}
            continue
        status, err = CHANNELS[name].send(user, s, n, http=http) if name in ("telegram", "whatsapp") else CHANNELS[name].send(user, s, n)
        out[name] = {"status": status, **({"error": err} if err else {}), "at": now.isoformat()}
    n.deliveries = out
    db.commit()
    return out
