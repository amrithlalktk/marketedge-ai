from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, ip_of, permissions_of
from app.core.config import get_settings
from app.core.db import get_db
from app.core import security as sec
from app.core.rbac import DEFAULT_ROLE
from app.models import Role, User, UserSession
from app.schemas import LoginIn, RegisterIn, TokenOut, TotpCode, TotpDisable, UserOut
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])
REFRESH_COOKIE = "me_refresh"
MAX_FAILED = 5
LOCK_MINUTES = 15
REUSE_GRACE_SECONDS = 10


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, full_name=u.full_name, role=u.role.name, permissions=sorted(permissions_of(u)), totp_enabled=u.totp_enabled)


def _issue(db: Session, user: User, response: Response, request: Request, family: str = None) -> TokenOut:
    s = get_settings()
    family = family or secrets.token_hex(16)
    raw = sec.new_refresh_token()
    db.add(UserSession(user_id=user.id, token_hash=sec.token_hash(raw), family=family,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=s.refresh_token_days),
                       user_agent=(request.headers.get("user-agent") or "")[:255], ip=ip_of(request)))
    db.commit()
    response.set_cookie(REFRESH_COOKIE, raw, httponly=True, secure=s.cookie_secure, samesite="strict",
                        max_age=s.refresh_token_days * 86400, path=f"{s.api_prefix}/auth")
    return TokenOut(access_token=sec.create_access_token(user.id, user.role.name, family), expires_in=s.access_token_minutes * 60)


@router.get("/config")
def auth_config():
    """Public: what the login page may offer."""
    return {"registration_open": get_settings().allow_registration}


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if not get_settings().allow_registration:
        raise HTTPException(403, "Sign-up is closed on this installation")
    problem = sec.validate_password_strength(body.password)
    if problem:
        raise HTTPException(422, problem)
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    role = db.scalar(select(Role).where(Role.name == DEFAULT_ROLE))
    user = User(email=email, password_hash=sec.hash_password(body.password), full_name=body.full_name, role_id=role.id)
    db.add(user)
    db.commit()
    db.refresh(user)
    audit(db, "auth.register", user.id, email, ip=ip_of(request))
    return user_out(user)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    now = datetime.now(timezone.utc)
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if user is None:
        sec.hash_password(body.password)  # equalise timing
        raise generic
    if user.locked_until and _aware(user.locked_until) > now:
        raise HTTPException(423, "Account temporarily locked after repeated failed logins. Try again later.")
    if not sec.verify_password(body.password, user.password_hash) or not user.is_active:
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until, user.failed_logins = now + timedelta(minutes=LOCK_MINUTES), 0
        db.commit()
        audit(db, "auth.login_failed", user.id, user.email, ip=ip_of(request))
        raise generic
    if user.totp_enabled:
        if not body.totp_code:
            raise HTTPException(401, "Two-factor code required", headers={"X-2FA-Required": "1"})
        if not sec.verify_totp(sec.decrypt(user.totp_secret_enc), body.totp_code):
            audit(db, "auth.2fa_failed", user.id, user.email, ip=ip_of(request))
            raise HTTPException(401, "Invalid two-factor code")
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    db.commit()
    audit(db, "auth.login", user.id, user.email, ip=ip_of(request))
    return _issue(db, user, response, request)


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(401, "No refresh session")
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == sec.token_hash(raw)))
    now = datetime.now(timezone.utc)
    if sess is None:
        raise HTTPException(401, "Invalid refresh session")
    live_successor = sess.revoked_at is not None and db.scalar(
        select(UserSession.id).where(UserSession.family == sess.family, UserSession.revoked_at.is_(None), UserSession.id > sess.id).limit(1))
    if live_successor and (now - _aware(sess.revoked_at)).total_seconds() <= REUSE_GRACE_SECONDS:
        # Benign race (two tabs refreshed together, token just rotated): reject without revoking the family.
        # Logged-out or theft-revoked families have no live successor and fall through to 401.
        raise HTTPException(409, "Session was just refreshed by another tab; retry with the current cookie")
    if sess.revoked_at is not None:
        # Reuse of a rotated token outside the grace window: assume theft, revoke the whole family.
        db.execute(update(UserSession).where(UserSession.family == sess.family, UserSession.revoked_at.is_(None)).values(revoked_at=now))
        db.commit()
        audit(db, "auth.refresh_reuse_detected", sess.user_id, sess.family, ip=ip_of(request))
        raise HTTPException(401, "Session revoked")
    if _aware(sess.expires_at) < now:
        raise HTTPException(401, "Session expired")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Account inactive")
    sess.revoked_at = now
    db.commit()
    return _issue(db, user, response, request, family=sess.family)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        sess = db.scalar(select(UserSession).where(UserSession.token_hash == sec.token_hash(raw)))
        if sess:
            db.execute(update(UserSession).where(UserSession.family == sess.family, UserSession.revoked_at.is_(None)).values(revoked_at=datetime.now(timezone.utc)))
            db.commit()
    response.delete_cookie(REFRESH_COOKIE, path=f"{get_settings().api_prefix}/auth")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.post("/2fa/setup")
def totp_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.totp_enabled:
        raise HTTPException(409, "Two-factor authentication is already enabled")
    secret = sec.new_totp_secret()
    user.totp_secret_enc = sec.encrypt(secret)
    db.commit()
    return {"secret": secret, "otpauth_uri": sec.totp_uri(secret, user.email)}


@router.post("/2fa/enable")
def totp_enable(body: TotpCode, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.totp_secret_enc or not sec.verify_totp(sec.decrypt(user.totp_secret_enc), body.code):
        raise HTTPException(400, "Invalid code")
    user.totp_enabled = True
    db.commit()
    audit(db, "auth.2fa_enabled", user.id, user.email, ip=ip_of(request))
    return {"totp_enabled": True}


@router.post("/2fa/disable")
def totp_disable(body: TotpDisable, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.totp_enabled or not sec.verify_password(body.password, user.password_hash) or not sec.verify_totp(sec.decrypt(user.totp_secret_enc), body.code):
        raise HTTPException(400, "Invalid password or code")
    user.totp_enabled, user.totp_secret_enc = False, None
    db.commit()
    audit(db, "auth.2fa_disabled", user.id, user.email, ip=ip_of(request))
    return {"totp_enabled": False}
