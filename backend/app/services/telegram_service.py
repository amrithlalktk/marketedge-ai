"""Telegram account linking: the user gets a one-time code and sends `/start <code>` to the bot.
Updates arrive via the secret-checked webhook or, if no webhook is set, the minute poller."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cache import get_cache
from app.core.config import get_settings
from app.models import NotificationSetting
from app.notify.channels import TelegramChannel
from app.services.notify_service import settings_for

LINK_TTL = timedelta(minutes=15)


def new_link_code(db: Session, user_id: int) -> dict:
    s = settings_for(db, user_id)
    s.telegram_link_code = secrets.token_hex(4).upper()
    s.telegram_link_expires = datetime.now(timezone.utc) + LINK_TTL
    db.commit()
    bot = get_settings().telegram_bot_username
    return {"code": s.telegram_link_code, "expires_at": s.telegram_link_expires.isoformat(), "bot": bot,
            "deep_link": f"https://t.me/{bot}?start={s.telegram_link_code}" if bot else None,
            "instructions": f"Send /start {s.telegram_link_code} to the MarketEdge bot" + (f" (@{bot})" if bot else "") + " within 15 minutes.",
            "configured": TelegramChannel().configured()}


def handle_update(db: Session, update: dict, http: Optional[httpx.Client] = None) -> Optional[int]:
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat = (msg.get("chat") or {}).get("id")
    if not text.startswith("/start") or chat is None:
        return None
    parts = text.split()
    if len(parts) < 2:
        return None
    code = parts[1].strip().upper()
    s = db.scalar(select(NotificationSetting).where(NotificationSetting.telegram_link_code == code))
    now = datetime.now(timezone.utc)
    if s is None:
        return None
    exp = s.telegram_link_expires if s.telegram_link_expires is None or s.telegram_link_expires.tzinfo else s.telegram_link_expires.replace(tzinfo=timezone.utc)
    if exp is None or exp < now:
        return None
    s.telegram_chat_id, s.telegram_link_code, s.telegram_link_expires = str(chat), None, None
    if "telegram" not in (s.default_channels or []):
        s.default_channels = list(s.default_channels or ["web"]) + ["telegram"]
    db.commit()
    tok = TelegramChannel().token()
    if tok:
        try:
            (http or httpx.Client(timeout=10)).post(f"https://api.telegram.org/bot{tok}/sendMessage",
                                                    json={"chat_id": chat, "text": "✅ Linked to MarketEdge. You will receive your alerts here."})
        except httpx.HTTPError:
            pass
    return s.user_id


def poll_updates(db: Session, http: Optional[httpx.Client] = None) -> dict:
    s = get_settings()
    tok = TelegramChannel().token()
    if not tok or s.telegram_webhook_secret:
        return {"skipped": "no bot token" if not tok else "webhook mode"}
    cache = get_cache()
    offset = int(cache.get_json("tg:offset") or 0)
    r = (http or httpx.Client(timeout=20)).get(f"https://api.telegram.org/bot{tok}/getUpdates", params={"offset": offset, "timeout": 0})
    linked = 0
    for u in r.json().get("result", []):
        offset = max(offset, int(u["update_id"]) + 1)
        linked += 1 if handle_update(db, u, http) else 0
    cache.set_json("tg:offset", offset, ttl=30 * 86400)
    return {"linked": linked, "offset": offset}
