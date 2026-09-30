"""Notification channels. Each returns (status, error) where status ∈ sent | failed | skipped | not_configured.

Secrets come from the encrypted provider key store (Admin → Providers) with environment
fallbacks; nothing here is exposed to clients.
    email     smtp / PASSWORD           + SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_FROM, SMTP_STARTTLS
    telegram  telegram / BOT_TOKEN      (+ TELEGRAM_BOT_USERNAME for the link instructions)
    push      webpush / VAPID_PRIVATE_KEY + VAPID_PUBLIC_KEY, VAPID_SUBJECT (requires `pywebpush`)
    whatsapp  whatsapp / ACCESS_TOKEN   + WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_TEMPLATE (approved template, user opt-in)
"""
from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Optional, Tuple

import httpx

from app.core.config import get_settings
from app.providers.registry import provider_secret

log = logging.getLogger(__name__)
Result = Tuple[str, Optional[str]]


def _secret(provider: str, name: str, env_value: Optional[str]) -> Optional[str]:
    return provider_secret(provider, name) or env_value


def _text(n) -> str:
    link = f"\n{get_settings().public_app_url.rstrip('/')}{n.link}" if n.link else ""
    return f"{n.title}\n\n{n.body}{link}\n\nAnalytical information only — not a recommendation or guarantee."


class WebChannel:
    name = "web"

    def configured(self) -> bool:
        return True

    def send(self, user, setting, n, http=None) -> Result:
        return "sent", None  # the stored notification IS the in-app delivery


class EmailChannel:
    name = "email"

    def configured(self) -> bool:
        s = get_settings()
        return bool(s.smtp_host and s.smtp_from)

    def send(self, user, setting, n, http=None, smtp_factory=smtplib.SMTP) -> Result:
        s = get_settings()
        if not self.configured():
            return "not_configured", None
        if not setting or not setting.email_enabled:
            return "skipped", "email notifications disabled by the user"
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = f"[MarketEdge] {n.title}", s.smtp_from, user.email  # only the account's own address
        msg.set_content(_text(n))
        try:
            with smtp_factory(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                if s.smtp_starttls:
                    smtp.starttls()
                pw = _secret("smtp", "PASSWORD", s.smtp_password)
                if s.smtp_user and pw:
                    smtp.login(s.smtp_user, pw)
                smtp.send_message(msg)
            return "sent", None
        except Exception as exc:  # network / auth errors are recorded, never raised into the scan
            return "failed", str(exc)[:300]


class TelegramChannel:
    name = "telegram"

    def token(self) -> Optional[str]:
        return _secret("telegram", "BOT_TOKEN", get_settings().telegram_bot_token)

    def configured(self) -> bool:
        return bool(self.token())

    def send(self, user, setting, n, http: Optional[httpx.Client] = None) -> Result:
        tok = self.token()
        if not tok:
            return "not_configured", None
        if not setting or not setting.telegram_chat_id:
            return "skipped", "Telegram not linked"
        client = http or httpx.Client(timeout=15)
        try:
            r = client.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                            json={"chat_id": setting.telegram_chat_id, "text": _text(n)[:4000], "disable_web_page_preview": True})
            if r.status_code == 200 and r.json().get("ok"):
                return "sent", None
            return "failed", f"Telegram {r.status_code}: {r.text[:200]}"
        except httpx.HTTPError as exc:
            return "failed", str(exc)[:300]


class PushChannel:
    name = "push"

    def configured(self) -> bool:
        s = get_settings()
        if not (s.vapid_public_key and _secret("webpush", "VAPID_PRIVATE_KEY", s.vapid_private_key)):
            return False
        try:
            import pywebpush  # noqa: F401
        except ImportError:
            return False
        return True

    def send(self, user, setting, n, http=None, webpush_fn=None) -> Result:
        s = get_settings()
        if not self.configured() and webpush_fn is None:
            return "not_configured", None
        subs = list(setting.push_subscriptions or []) if setting else []
        if not subs:
            return "skipped", "no browser subscribed"
        if webpush_fn is None:
            from pywebpush import WebPushException, webpush as webpush_fn  # noqa: N813
        else:
            WebPushException = Exception  # noqa: N806
        data = json.dumps({"title": n.title, "body": n.body[:300], "url": n.link or "/"})
        ok, keep = 0, []
        for sub in subs:
            try:
                webpush_fn(subscription_info=sub, data=data, vapid_private_key=_secret("webpush", "VAPID_PRIVATE_KEY", s.vapid_private_key),
                           vapid_claims={"sub": s.vapid_subject})
                ok += 1
                keep.append(sub)
            except WebPushException as exc:  # 404/410 → subscription gone; drop it
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status not in (404, 410):
                    keep.append(sub)
        setting.push_subscriptions = keep
        return ("sent", None) if ok else ("failed", "no subscription accepted the message")


class WhatsAppChannel:
    name = "whatsapp"

    def configured(self) -> bool:
        s = get_settings()
        return bool(s.whatsapp_phone_number_id and s.whatsapp_template and _secret("whatsapp", "ACCESS_TOKEN", s.whatsapp_access_token))

    def send(self, user, setting, n, http: Optional[httpx.Client] = None) -> Result:
        s = get_settings()
        if not self.configured():
            return "not_configured", None
        if not setting or not setting.whatsapp_number:
            return "skipped", "no opted-in WhatsApp number"
        client = http or httpx.Client(timeout=15)
        body = {"messaging_product": "whatsapp", "to": setting.whatsapp_number, "type": "template",
                "template": {"name": s.whatsapp_template, "language": {"code": "en"},
                             "components": [{"type": "body", "parameters": [{"type": "text", "text": n.title[:60]}, {"type": "text", "text": n.body[:900]}]}]}}
        try:
            r = client.post(f"https://graph.facebook.com/v20.0/{s.whatsapp_phone_number_id}/messages", json=body,
                            headers={"Authorization": f"Bearer {_secret('whatsapp', 'ACCESS_TOKEN', s.whatsapp_access_token)}"})
            return ("sent", None) if r.status_code in (200, 201) else ("failed", f"WhatsApp {r.status_code}: {r.text[:200]}")
        except httpx.HTTPError as exc:
            return "failed", str(exc)[:300]


CHANNELS = {c.name: c for c in (WebChannel(), EmailChannel(), TelegramChannel(), PushChannel(), WhatsAppChannel())}


def channel_status() -> dict:
    return {k: {"configured": c.configured()} for k, c in CHANNELS.items()}
