"""Background tasks. Each task records its lifecycle in the `jobs` table."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import Job
from app.providers.registry import market_provider
from app.services import backtest_service, events_service, market_data, options_service, scan_service

from .celery_app import celery

log = logging.getLogger(__name__)


def _run_job(job_id: Optional[int], kind: str, fn, **params):
    db = SessionLocal()
    try:
        job = db.get(Job, job_id) if job_id else None
        if job is None:
            job = Job(kind=kind, params=params)
            db.add(job)
        job.status, job.started_at = "running", datetime.now(timezone.utc)
        db.commit()
        try:
            result = fn(db, **params)
            job.status, job.result = "done", result
        except Exception as exc:
            log.exception("job %s failed", kind)
            db.rollback()
            job = db.get(Job, job.id)
            job.status, job.error = "failed", str(exc)[:2000]
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return job.id
    finally:
        db.close()


def _ingest(db, market: Optional[str] = None, full: bool = False, symbols=None):
    market = market or get_settings().market
    return market_data.ingest(db, market_provider(market=market), market, symbols=symbols, full=full)


def _scan(db, market: Optional[str] = None):
    run = scan_service.run_scan(db, market)
    return {"scan_run_id": run.id, **(run.stats or {})}


def _backtest(db, backtest_id: int):
    bt = backtest_service.execute_backtest(db, backtest_id)
    return {"backtest_id": bt.id, "status": bt.status}


@celery.task(name="app.workers.tasks.ingest")
def ingest(job_id: Optional[int] = None, market: Optional[str] = None, full: bool = False):
    return _run_job(job_id, "ingest", _ingest, market=market, full=full)


@celery.task(name="app.workers.tasks.scan")
def scan(job_id: Optional[int] = None, market: Optional[str] = None):
    return _run_job(job_id, "scan", _scan, market=market)


def _options(db):
    ing = options_service.ingest_chain(db)
    run = options_service.run_options(db)
    return {"ingest": ing, "scan_run_id": run.id, **(run.stats or {})}


@celery.task(name="app.workers.tasks.options")
def options(job_id: Optional[int] = None):
    return _run_job(job_id, "options", _options)


@celery.task(name="app.workers.tasks.ingest_and_scan")
def ingest_and_scan(market: Optional[str] = None):
    """Scheduled per market. FX first so non-INR markets convert with fresh rates."""
    from app.core.markets import enabled_markets

    market = market or "NSE"
    s = get_settings()
    if market not in enabled_markets():
        return {"skipped": f"{market} is not enabled (MARKETS_ENABLED)"}
    ingest(None, market)
    if s.feature_on("news"):
        news(None, market)  # fresh headlines/sentiment before the scan attaches them to setups
    scan(None, market)
    if market == "NSE":
        if s.feature_on("options"):
            options(None)
        db = SessionLocal()
        try:  # one message per session with today's ideas (or "No trade today"), after options so both are in it
            from app.services.alert_service import daily_ideas_alerts

            return {"daily_ideas_sent": daily_ideas_alerts(db)}
        finally:
            db.close()


@celery.task(name="app.workers.tasks.backtest")
def backtest(job_id: Optional[int], backtest_id: int):
    return _run_job(job_id, "backtest", _backtest, backtest_id=backtest_id)


def _news(db, market: str = "NSE"):
    return events_service.ingest_news(db, market)


def _calendar(db):
    from app.core.markets import enabled_markets

    return events_service.ingest_calendar(db, enabled_markets())


@celery.task(name="app.workers.tasks.news")
def news(job_id: Optional[int] = None, market: str = "NSE"):
    return _run_job(job_id, "news", _news, market=market)


@celery.task(name="app.workers.tasks.calendar")
def calendar(job_id: Optional[int] = None):
    return _run_job(job_id, "calendar", _calendar)


@celery.task(name="app.workers.tasks.news_all")
def news_all():
    from app.core.markets import enabled_markets

    for m in enabled_markets():
        news(None, m)


def _ml_train(db, market: str = "NSE", user_id=None):
    from app.services.ml_service import train

    m = train(db, market, user_id)
    return {"model_id": m.id, "market": market, "version": m.version, "algo": m.algo, "eligible": m.eligible, "gate": m.metrics.get("gate")}


@celery.task(name="app.workers.tasks.ml_train")
def ml_train(job_id: Optional[int] = None, market: str = "NSE", user_id: Optional[int] = None):
    return _run_job(job_id, "ml_train", _ml_train, market=market, user_id=user_id)


def _tick(db):
    from app.services import alert_service, portfolio_service

    return {"paper": portfolio_service.process(db), "alerts": alert_service.evaluate_bar_alerts(db)}


@celery.task(name="app.workers.tasks.alerts_tick")
def alerts_tick():
    """Every few minutes: advance paper trades and evaluate price alerts on the latest stored bars."""
    db = SessionLocal()
    try:
        return _tick(db)
    finally:
        db.close()


@celery.task(name="app.workers.tasks.retry_notifications")
def retry_notifications():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.models import Notification
    from app.services.notify_service import deliver

    db = SessionLocal()
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=6)
        n = 0
        for row in db.scalars(select(Notification).where(Notification.created_at >= since)):
            if any(v.get("status") == "failed" for v in (row.deliveries or {}).values()):
                deliver(db, row.id)
                n += 1
        return {"retried": n}
    finally:
        db.close()


@celery.task(name="app.workers.tasks.telegram_poll")
def telegram_poll():
    """Fallback when no webhook is configured: read bot updates and complete /start <code> links."""
    from app.services.telegram_service import poll_updates

    db = SessionLocal()
    try:
        return poll_updates(db)
    finally:
        db.close()


def retention_cleanup_db(db, now: Optional[datetime] = None) -> dict:
    """Delete audit logs, notifications and job records past their retention window."""
    from datetime import timedelta

    from sqlalchemy import delete

    from app.models import AuditLog, Notification

    s = get_settings()
    now = now or datetime.now(timezone.utc)
    out = {
        "audit_logs": db.execute(delete(AuditLog).where(AuditLog.created_at < now - timedelta(days=s.audit_retention_days))).rowcount,
        "notifications": db.execute(delete(Notification).where(Notification.created_at < now - timedelta(days=s.notification_retention_days))).rowcount,
        "jobs": db.execute(delete(Job).where(Job.created_at < now - timedelta(days=90), Job.status.in_(("done", "failed")))).rowcount,
    }
    db.commit()
    return out


@celery.task(name="app.workers.tasks.retention_cleanup")
def retention_cleanup():
    db = SessionLocal()
    try:
        return retention_cleanup_db(db)
    finally:
        db.close()


def upstox_reminder_db(db) -> dict:
    """Weekdays before the NSE scan: if Upstox feeds NSE/options but today's token is missing, tell every admin."""
    from app.models import Role, User
    from app.providers.registry import _UPSTOX_MEMO, upstox_client
    from app.providers.upstox import UpstoxAuthError
    from app.services.notify_service import notify

    s = get_settings()
    if "upstox" not in (s.market_data_provider, s.options_data_provider):
        return {"skipped": "upstox not in use"}
    from datetime import timedelta as _td

    from app.providers.registry import upstox_token

    _UPSTOX_MEMO.update(at=None)
    title = "Upstox token not working: paste a new one before the 18:30 NSE scan"
    try:
        from app.providers.upstox import UpstoxAuthError as _UAE
        from app.providers.upstox import token_rejected_at, verify_token

        upstox_client().preflight()
        t = upstox_token() or {}
        try:  # a stored token can be revoked/regenerated long before its expiry date: ask Upstox
            verify_token(t["access_token"])
        except _UAE:
            raise
        except Exception as exc:  # network hiccup: do not alarm
            log.warning("Upstox token check skipped: %s", exc)
        if token_rejected_at(t.get("access_token")):
            raise _UAE("rejected")
        exp = t.get("expires_at")
        if t.get("kind") != "analytics" or not exp or datetime.fromisoformat(exp) - datetime.now(timezone.utc) > _td(days=14):
            return {"connected": True}
        title = "Upstox analytics token expires within 14 days — generate a new one"
    except UpstoxAuthError:
        pass
    admins = db.query(User).join(Role, Role.id == User.role_id).filter(Role.name == "admin", User.is_active.is_(True)).all()
    for u in admins:
        notify(db, u.id, title,
               "Without a valid Upstox token the NSE/options ingest is skipped and setups are marked DATA DELAYED. "
               "Paste a new Analytics token (valid 1 year) or connect for today in Admin → Providers.", link="/admin?tab=providers")
    return {"connected": False, "notified": len(admins)}


@celery.task(name="app.workers.tasks.upstox_reminder")
def upstox_reminder():
    db = SessionLocal()
    try:
        return upstox_reminder_db(db)
    finally:
        db.close()
