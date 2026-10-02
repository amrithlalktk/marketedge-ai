"""Daily Upstox connection (OAuth2 authorization-code). Tokens expire at 03:30 IST; there is no refresh token."""
from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ip_of, require
from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import encrypt
from app.models import ProviderCredential, User
from app.providers import registry
from datetime import timedelta

from pydantic import BaseModel, Field

from app.providers.upstox import IST, UpstoxAuthError, UpstoxError, authorize_url, exchange_code, token_rejected_at, verify_token
from app.services.audit import audit

router = APIRouter(prefix="/upstox", tags=["providers"])
STATE_TTL = 600


def _store(db: Session, name: str, value: str, user_id) -> None:
    row = db.scalar(select(ProviderCredential).where(ProviderCredential.provider == "upstox", ProviderCredential.name == name))
    if row is None:
        row = ProviderCredential(provider="upstox", name=name, created_by=user_id, encrypted_value="")
        db.add(row)
    row.encrypted_value, row.enabled = encrypt(value), True


class AnalyticsTokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=4096)


@router.get("/status", dependencies=[Depends(require("admin:providers"))])
def status():
    s = get_settings()
    now = datetime.now(IST)
    a_exp = registry.provider_secret("upstox", "ANALYTICS_TOKEN_EXPIRES")
    analytics = bool(registry.provider_secret("upstox", "ANALYTICS_TOKEN")) and (not a_exp or datetime.fromisoformat(a_exp) > now)
    configured = bool(registry.provider_secret("upstox", "API_KEY") and registry.provider_secret("upstox", "API_SECRET"))
    exp = registry.provider_secret("upstox", "ACCESS_TOKEN_EXPIRES")
    daily = bool(exp and registry.provider_secret("upstox", "ACCESS_TOKEN") and datetime.fromisoformat(exp) > now)
    current = registry.provider_secret("upstox", "ANALYTICS_TOKEN" if analytics else "ACCESS_TOKEN")
    rejected = token_rejected_at(current) if (analytics or daily) else None
    return {"mode": "analytics" if analytics else "daily" if daily else None, "connected": (analytics or daily) and not rejected,
            "rejected_at": rejected,
            "expires_at": a_exp if analytics else exp if daily else None, "configured": configured, "redirect_uri": s.upstox_redirect_uri,
            "used_for": {"market_data": s.market_data_provider == "upstox", "options": s.options_data_provider == "upstox"},
            "note": ("Analytics token: read-only, valid 1 year, no daily login." if analytics else
                     "Paste an analytics token (valid 1 year), or connect daily (tokens expire at 03:30 IST).")}


@router.put("/analytics-token")
def put_analytics_token(body: AnalyticsTokenIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin:providers"))):
    """Store the 1-year read-only Analytics Token (Upstox Developer Apps → Analytics → Generate Token) after checking it works."""
    tok = body.token.strip()
    try:
        verify_token(tok)
    except UpstoxAuthError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (UpstoxError, Exception) as exc:  # network issue: do not store an unverified token
        raise HTTPException(502, f"Could not verify the token with Upstox: {str(exc)[:200]}") from exc
    expires = (datetime.now(IST) + timedelta(days=365)).isoformat()  # generation date ≈ now (1-year validity)
    _store(db, "ANALYTICS_TOKEN", tok, user.id)
    _store(db, "ANALYTICS_TOKEN_EXPIRES", expires, user.id)
    db.commit()
    registry._UPSTOX_MEMO.update(at=None)
    audit(db, "provider.upstox.analytics_token", user.id, "upstox", {"expires_at": expires}, ip_of(request))
    return {"stored": True, "expires_at": expires}


@router.post("/connect")
def connect(user: User = Depends(require("admin:providers"))):
    client_id = registry.provider_secret("upstox", "API_KEY")
    if not client_id or not registry.provider_secret("upstox", "API_SECRET"):
        raise HTTPException(409, "Add upstox / API_KEY and upstox / API_SECRET in Admin → Providers first")
    state = secrets.token_urlsafe(24)
    get_cache().set_json(f"upstox:state:{state}", {"user_id": user.id}, ttl=STATE_TTL)
    return {"url": authorize_url(client_id, get_settings().upstox_redirect_uri, state)}


@router.get("/callback", include_in_schema=False)
def callback(request: Request, code: str = Query("", max_length=512), state: str = Query("", max_length=128), db: Session = Depends(get_db)):
    """Upstox redirects the browser here. `state` (single-use, 10 min, issued to a logged-in admin) prevents CSRF."""
    back = "/admin?tab=providers&upstox="
    key = f"upstox:state:{state}"
    st = get_cache().get_json(key) if state else None
    if not st:
        return RedirectResponse(back + "error&reason=expired", status_code=303)
    get_cache().set_json(key, None, ttl=1)  # single use
    if not code:
        return RedirectResponse(back + "error&reason=denied", status_code=303)
    try:
        tok = exchange_code(code, registry.provider_secret("upstox", "API_KEY"), registry.provider_secret("upstox", "API_SECRET"),
                            get_settings().upstox_redirect_uri)
    except UpstoxError:
        return RedirectResponse(back + "error&reason=exchange", status_code=303)
    _store(db, "ACCESS_TOKEN", tok["access_token"], st["user_id"])
    _store(db, "ACCESS_TOKEN_EXPIRES", tok["expires_at"], st["user_id"])
    db.commit()
    registry._UPSTOX_MEMO.update(at=None)  # pick up the new token immediately in this process
    audit(db, "provider.upstox.connect", st["user_id"], "upstox", {"expires_at": tok["expires_at"]}, ip_of(request))
    return RedirectResponse(back + "connected", status_code=303)
