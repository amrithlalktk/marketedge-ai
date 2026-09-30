from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import ip_of, require
from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.db import get_db
from app.core.observability import queue_depths, readiness
from app.models import AuditLog, Job, ProviderCredential, Role, ScanRun, User
from app.core.security import encrypt
from app.providers.registry import FUNDAMENTAL_PROVIDERS, MARKET_PROVIDERS, market_provider
from app.schemas import ProviderKeyIn, RoleChange, ScoringSettingsIn, StrategyControlIn
from app.services.audit import audit
from app.services.market_data import provider_status
from app.services.settings_service import (engine_config, engine_overrides, save_engine_overrides, set_strategy_enabled,
                                           strategy_controls)
from app.workers import tasks

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health", dependencies=[Depends(require("admin:jobs"))])
def health(db: Session = Depends(get_db)):
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    last = db.scalar(select(ScanRun).order_by(ScanRun.id.desc()).limit(1))
    return {"database": db_ok, "cache_backend": get_cache().backend, "redis": get_cache().ping(),
            "provider": market_provider().health(), "data_sources": provider_status(db),
            "last_scan": {"id": last.id, "status": last.status, "as_of": str(last.as_of) if last.as_of else None,
                          "started_at": last.started_at.isoformat(), "error": last.error, "stats": last.stats} if last else None,
            "failed_jobs": db.scalar(select(func.count()).select_from(Job).where(Job.status == "failed")),
            "readiness": readiness(), "queues": queue_depths()}


@router.get("/settings/engine", dependencies=[Depends(require("admin:settings"))])
def get_engine_settings(db: Session = Depends(get_db)):
    return {"overrides": engine_overrides(db), "effective": engine_config(db).to_dict()}


@router.put("/settings/engine")
def put_engine_settings(body: ScoringSettingsIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin:settings"))):
    value = {**engine_overrides(db), **body.model_dump(exclude_none=True)}
    cfg = save_engine_overrides(db, value, user.id)
    audit(db, "settings.engine_update", user.id, "engine_config", value, ip_of(request))
    get_cache().invalidate_prefix("me:")
    return {"overrides": value, "effective": cfg.to_dict(), "note": "Changes apply from the next scan."}


@router.get("/providers", dependencies=[Depends(require("admin:providers"))])
def providers(db: Session = Depends(get_db)):
    s = get_settings()
    keys = db.scalars(select(ProviderCredential)).all()
    from app.providers.registry import OPTIONS_PROVIDERS

    return {"market_data": {"active": s.market_data_provider, "available": sorted(MARKET_PROVIDERS)},
            "options": {"active": s.options_data_provider, "available": sorted(OPTIONS_PROVIDERS), "underlying": s.options_underlying},
            "fundamentals": {"active": s.fundamentals_provider, "available": sorted(FUNDAMENTAL_PROVIDERS)},
            "api_keys": [{"id": k.id, "provider": k.provider, "name": k.name, "enabled": k.enabled, "created_at": k.created_at.isoformat()} for k in keys],
            "status": provider_status(db)}


@router.post("/providers/keys", status_code=201)
def add_key(body: ProviderKeyIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin:providers"))):
    row = db.scalar(select(ProviderCredential).where(ProviderCredential.provider == body.provider, ProviderCredential.name == body.name))
    if row is None:
        row = ProviderCredential(provider=body.provider, name=body.name, created_by=user.id, encrypted_value="")
        db.add(row)
    row.encrypted_value = encrypt(body.value)  # never returned by any endpoint
    db.commit()
    audit(db, "provider.key_set", user.id, f"{body.provider}:{body.name}", ip=ip_of(request))
    return {"id": row.id, "provider": row.provider, "name": row.name}


@router.post("/jobs/ingest", status_code=202)
def run_ingest(request: Request, full: bool = False, market: str = Query("NSE", pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$"),
               db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    job = Job(kind="ingest", params={"full": full, "market": market}, created_by=user.id)
    db.add(job)
    db.commit()
    audit(db, "job.ingest", user.id, str(job.id), ip=ip_of(request))
    tasks.ingest.delay(job.id, market, full)
    return {"job_id": job.id}


@router.post("/jobs/scan", status_code=202)
def run_scan(request: Request, market: str = Query("NSE", pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$"),
             db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    job = Job(kind="scan", params={"market": market}, created_by=user.id)
    db.add(job)
    db.commit()
    audit(db, "job.scan", user.id, str(job.id), ip=ip_of(request))
    tasks.scan.delay(job.id, market)
    return {"job_id": job.id}


@router.post("/jobs/options", status_code=202)
def run_options(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    job = Job(kind="options", params={}, created_by=user.id)
    db.add(job)
    db.commit()
    audit(db, "job.options", user.id, str(job.id), ip=ip_of(request))
    tasks.options.delay(job.id)
    return {"job_id": job.id}


@router.post("/jobs/news", status_code=202)
def run_news(request: Request, market: str = Query("NSE", pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$"),
             db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    job = Job(kind="news", params={"market": market}, created_by=user.id)
    db.add(job)
    db.commit()
    audit(db, "job.news", user.id, str(job.id), ip=ip_of(request))
    tasks.news.delay(job.id, market)
    return {"job_id": job.id}


@router.post("/jobs/calendar", status_code=202)
def run_calendar(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    job = Job(kind="calendar", params={}, created_by=user.id)
    db.add(job)
    db.commit()
    audit(db, "job.calendar", user.id, str(job.id), ip=ip_of(request))
    tasks.calendar.delay(job.id)
    return {"job_id": job.id}


@router.post("/jobs/ml-train", status_code=202)
def run_ml_train(request: Request, market: str = Query("NSE", pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$"),
                 db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    job = Job(kind="ml_train", params={"market": market}, created_by=user.id)
    db.add(job)
    db.commit()
    audit(db, "job.ml_train", user.id, str(job.id), ip=ip_of(request))
    tasks.ml_train.delay(job.id, market, user.id)
    return {"job_id": job.id}


@router.post("/jobs/paper-tick")
def run_paper_tick(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    """Advance paper trades and evaluate alerts on the bars stored right now (normally every 5 minutes)."""
    from app.services import alert_service, portfolio_service

    audit(db, "job.paper_tick", user.id, "", ip=ip_of(request))
    return {"paper": portfolio_service.process(db), "alerts": alert_service.evaluate_bar_alerts(db)}


@router.get("/jobs", dependencies=[Depends(require("admin:jobs"))])
def jobs(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    rows = db.scalars(select(Job).order_by(Job.id.desc()).limit(limit))
    return {"items": [{"id": j.id, "kind": j.kind, "status": j.status, "params": j.params, "result": j.result, "error": j.error,
                       "created_at": j.created_at.isoformat(), "finished_at": j.finished_at.isoformat() if j.finished_at else None} for j in rows]}


@router.get("/jobs/{job_id}", dependencies=[Depends(require("admin:jobs"))])
def job(job_id: int, db: Session = Depends(get_db)):
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "Job not found")
    return {"id": j.id, "kind": j.kind, "status": j.status, "result": j.result, "error": j.error}


@router.get("/users", dependencies=[Depends(require("admin:users"))])
def users(db: Session = Depends(get_db), page: int = Query(1, ge=1)):
    rows = db.scalars(select(User).order_by(User.id).offset((page - 1) * 100).limit(100))
    return {"items": [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role.name, "is_active": u.is_active,
                       "totp_enabled": u.totp_enabled, "created_at": u.created_at.isoformat(),
                       "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None} for u in rows]}


@router.patch("/users/{user_id}")
def change_user(user_id: int, body: RoleChange, request: Request, db: Session = Depends(get_db), admin: User = Depends(require("admin:users"))):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.id == admin.id and (body.role != "admin" or body.is_active is False):
        raise HTTPException(400, "You cannot demote or deactivate your own account")
    u.role_id = db.scalar(select(Role.id).where(Role.name == body.role))
    if body.is_active is not None:
        u.is_active = body.is_active
    db.commit()
    audit(db, "user.update", admin.id, u.email, body.model_dump(), ip_of(request))
    return {"id": u.id, "role": body.role, "is_active": u.is_active}


@router.get("/audit-logs", dependencies=[Depends(require("audit:read"))])
def audit_logs(db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=500), action: str = None):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if action:
        q = q.where(AuditLog.action == action)
    return {"items": [{"id": a.id, "user_id": a.user_id, "action": a.action, "target": a.target, "detail": a.detail, "ip": a.ip,
                       "created_at": a.created_at.isoformat()} for a in db.scalars(q)]}


@router.get("/strategy-controls", dependencies=[Depends(require("admin:settings"))])
def get_strategy_controls(db: Session = Depends(get_db)):
    """Strategies switched off for live setups, per market (still backtested every scan)."""
    return {"disabled": strategy_controls(db)}


@router.put("/strategy-controls/{market}/{strategy_id}")
def put_strategy_control(market: str, strategy_id: str, body: StrategyControlIn, request: Request, db: Session = Depends(get_db),
                         admin: User = Depends(require("admin:settings"))):
    from app.core.markets import enabled_markets
    from app.services.strategy_service import resolve

    if market not in enabled_markets():
        raise HTTPException(404, f"Unknown or disabled market {market!r}")
    try:
        resolve(db, strategy_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    per = set_strategy_enabled(db, market, strategy_id, body.enabled, body.reason, admin.id)
    audit(db, "strategy.enable" if body.enabled else "strategy.disable", admin.id, f"{market}/{strategy_id}", {"reason": body.reason}, ip_of(request))
    get_cache().invalidate_prefix("me:")
    return {"market": market, "strategy_id": strategy_id, "enabled": body.enabled, "disabled": per,
            "note": "Takes effect on the next scan; the strategy's historical statistics keep updating either way."}
