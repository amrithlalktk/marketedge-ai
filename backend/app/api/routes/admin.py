from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import ip_of, require
from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.db import get_db
from app.core.observability import readiness
from app.models import AuditLog, Job, ProviderCredential, Role, ScanRun, User
from app.core.security import encrypt
from app.providers.registry import MARKET_PROVIDERS, market_provider
from app.schemas import ProviderKeyIn, RoleChange, ScoringSettingsIn, StrategyControlIn
from app.services.audit import audit
from app.services.market_data import provider_status
from app.services.settings_service import (engine_config, engine_overrides, save_engine_overrides, set_strategy_enabled,
                                           strategy_controls)

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
            "readiness": readiness()}


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
            "crypto": {"active": s.crypto_data_provider, "available": ["binance", "sample"]},
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


def _job(request: Request, db: Session, user: User, kind: str, **params) -> dict:
    """Run a job now. JOB_RUNNER=inline (local/tests): executed in this request. JOB_RUNNER=github (Vercel): the daily
    GitHub Actions workflow is dispatched instead, because a serverless request cannot run for up to an hour."""
    from app import jobs

    audit(db, f"job.{kind}", user.id, str(params), ip=ip_of(request))
    s = get_settings()
    if s.job_runner == "github":
        try:
            return jobs.dispatch_github(params.get("market", "NSE"), bool(params.get("full")))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
    fn = {"ingest": jobs.ingest, "scan": jobs.scan, "options": jobs.options, "daily": jobs.daily}[kind]
    job = jobs.run_job(db, kind, fn, created_by=user.id, **params)
    return {"job_id": job.id, "status": job.status}


MARKET = Query("NSE", pattern="^(NSE|CRYPTO)$")


@router.post("/jobs/ingest", status_code=202)
def run_ingest(request: Request, full: bool = False, market: str = MARKET, db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    return _job(request, db, user, "ingest", market=market, full=full)


@router.post("/jobs/scan", status_code=202)
def run_scan(request: Request, market: str = MARKET, db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    return _job(request, db, user, "scan", market=market)


@router.post("/jobs/options", status_code=202)
def run_options(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    return _job(request, db, user, "options")


@router.post("/jobs/daily", status_code=202)
def run_daily(request: Request, market: str = MARKET, full: bool = False, db: Session = Depends(get_db), user: User = Depends(require("admin:jobs"))):
    """The whole end-of-day pipeline for one market (what the schedule runs)."""
    return _job(request, db, user, "daily", market=market, full=full)


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


@router.get("/strategies", dependencies=[Depends(require("admin:settings"))])
def strategies(market: str = Query("NSE", pattern="^(NSE|CRYPTO)$"), db: Session = Depends(get_db)):
    """Built-in strategies with their latest historical performance in `market` and whether they are switched off there."""
    from app.api.routes.markets import latest_snapshot
    from engine.strategies import STRATEGIES

    perf = {p["strategy"]["id"]: p for p in (latest_snapshot(db, "strategy_performance", market) or {}).get("strategies", [])}
    off = strategy_controls(db).get(market) or {}

    def panel(p):
        if not p:
            return None
        sm = p.get("summary", {})
        return {"backtest_period": p.get("backtest_period"), "trades": sm.get("sample_size"), "t1_hit_rate": sm.get("t1_hit_rate"),
                "stop_rate": sm.get("stop_rate"), "profit_factor": sm.get("profit_factor"), "expectancy_r": sm.get("expectancy_r"),
                "segments": p.get("segments"), "warnings": p.get("warnings")}
    return {"market": market, "items": [{**spec.public(), "performance": panel(perf.get(sid)), "disabled": off.get(sid)}
                                        for sid, spec in STRATEGIES.items()]}


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
