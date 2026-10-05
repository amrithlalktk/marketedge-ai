"""Jobs as plain functions — no queue, no always-on workers.

Production (Vercel + Neon): the daily pipeline runs in GitHub Actions on a schedule
(`python -m app.cli daily NSE` at 18:30 IST, `... daily CRYPTO` at 06:00 IST). The Admin
"run now" button dispatches that workflow (JOB_RUNNER=github). Locally / in tests jobs
run inline (JOB_RUNNER=inline). Every job is recorded in the `jobs` table.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Job

log = logging.getLogger(__name__)


def run_job(db: Session, kind: str, fn: Callable, job: Optional[Job] = None, created_by: Optional[int] = None, **params) -> Job:
    """Run fn(db, **params) and record it. Never raises: failures are stored on the job."""
    if job is None:
        job = Job(kind=kind, params=params, created_by=created_by)
        db.add(job)
    job.status, job.started_at = "running", datetime.now(timezone.utc)
    db.commit()
    try:
        job.result, job.status = fn(db, **params), "done"
    except Exception as exc:
        log.exception("job %s failed", kind)
        db.rollback()
        job = db.get(Job, job.id)
        job.status, job.error = "failed", str(exc)[:2000]
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    return job


# ---------------------------------------------------------------- steps
def ingest(db: Session, market: str = "NSE", full: bool = False) -> dict:
    from app.providers.registry import market_provider
    from app.services import market_data

    return market_data.ingest(db, market_provider(market=market), market, full=full)


def scan(db: Session, market: str = "NSE") -> dict:
    from app.services import scan_service

    run = scan_service.run_scan(db, market)
    return {"scan_run_id": run.id, **(run.stats or {})}


def options(db: Session) -> dict:
    from app.services import options_service

    ing = options_service.ingest_chain(db)
    run = options_service.run_options(db)
    return {"ingest": ing, "scan_run_id": run.id, **(run.stats or {})}


def retention(db: Session, now: Optional[datetime] = None) -> dict:
    """Delete audit logs, notifications, finished job records and superseded scan events past their windows."""
    from sqlalchemy import delete, select

    from app.models import AuditLog, Backtest, Notification, ScanRun

    s = get_settings()
    now = now or datetime.now(timezone.utc)
    out = {
        "audit_logs": db.execute(delete(AuditLog).where(AuditLog.created_at < now - timedelta(days=s.audit_retention_days))).rowcount,
        "notifications": db.execute(delete(Notification).where(Notification.created_at < now - timedelta(days=s.notification_retention_days))).rowcount,
        "jobs": db.execute(delete(Job).where(Job.created_at < now - timedelta(days=90), Job.status.in_(("done", "failed")))).rowcount,
    }
    # each scan stores its full historical event set; only the latest few per market are ever read (free DB tier: keep it small)
    keep = set()
    for market in {m for (m,) in db.execute(select(ScanRun.market).distinct())}:
        keep |= {bid for (bid,) in db.execute(select(ScanRun.events_backtest_id).where(ScanRun.market == market, ScanRun.events_backtest_id.isnot(None))
                                              .order_by(ScanRun.id.desc()).limit(2))}
    used = {bid for (bid,) in db.execute(select(ScanRun.events_backtest_id).where(ScanRun.events_backtest_id.isnot(None)))}
    stale = used - keep
    if stale:
        db.execute(ScanRun.__table__.update().where(ScanRun.events_backtest_id.in_(stale)).values(events_backtest_id=None))
        from app.models import BacktestTrade

        db.execute(delete(BacktestTrade).where(BacktestTrade.backtest_id.in_(stale)))
        out["event_sets"] = db.execute(delete(Backtest).where(Backtest.id.in_(stale))).rowcount
    db.commit()
    return out


def upstox_check(db: Session) -> dict:
    """Before the NSE run: if Upstox feeds NSE/options but the token is missing, rejected or about to expire, tell every admin.
    (Daily candles are public, so prices still load; quotes and the option chain need the token.)"""
    from app.models import Role, User
    from app.providers.registry import _UPSTOX_MEMO, upstox_client, upstox_token
    from app.providers.upstox import UpstoxAuthError, token_rejected_at, verify_token
    from app.services.notify_service import notify

    s = get_settings()
    if "upstox" not in (s.market_data_provider, s.options_data_provider):
        return {"skipped": "upstox not in use"}
    _UPSTOX_MEMO.update(at=None)
    title = "Upstox token not working: paste a new one in Admin → Providers"
    try:
        upstox_client().preflight()
        t = upstox_token() or {}
        try:
            verify_token(t["access_token"])
        except UpstoxAuthError:
            raise
        except Exception as exc:  # network hiccup: do not alarm
            log.warning("Upstox token check skipped: %s", exc)
        if token_rejected_at(t.get("access_token")):
            raise UpstoxAuthError("rejected")
        exp = t.get("expires_at")
        if t.get("kind") != "analytics" or not exp or datetime.fromisoformat(exp) - datetime.now(timezone.utc) > timedelta(days=14):
            return {"connected": True}
        title = "Upstox analytics token expires within 14 days — generate a new one"
    except UpstoxAuthError:
        pass
    admins = db.query(User).join(Role, Role.id == User.role_id).filter(Role.name == "admin", User.is_active.is_(True)).all()
    for u in admins:
        notify(db, u.id, title, "Without a valid Upstox token the NIFTY option chain cannot be read and today's stock bar is missing. "
               "Paste a new Analytics token (valid 1 year) in Admin → Providers.", link="/admin?tab=providers")
    return {"connected": False, "notified": len(admins)}


def retry_notifications(db: Session) -> dict:
    from sqlalchemy import select

    from app.models import Notification
    from app.services.notify_service import deliver

    since = datetime.now(timezone.utc) - timedelta(days=1)
    n = 0
    for row in db.scalars(select(Notification).where(Notification.created_at >= since)):
        if any(v.get("status") == "failed" for v in (row.deliveries or {}).values()):
            deliver(db, row.id)
            n += 1
    return {"retried": n}


# ---------------------------------------------------------------- the daily pipeline
def daily(db: Session, market: str, full: bool = False) -> dict:
    """One market's end-of-day run: [token check] → ingest → scan (+ paper fills and bar alerts) → [NIFTY options →
    daily ideas message] → retention. Each step is its own recorded job; a failed step does not stop the next."""
    from app.core.markets import enabled_markets

    if market not in enabled_markets():
        return {"skipped": f"{market} is not enabled"}
    out = {}
    if market == "NSE":
        out["upstox"] = run_job(db, "upstox_check", upstox_check).result
    out["ingest"] = run_job(db, "ingest", ingest, market=market, full=full).status
    out["scan"] = run_job(db, "scan", scan, market=market).status
    if market == "NSE":
        out["options"] = run_job(db, "options", options).status
        from app.services.alert_service import daily_ideas_alerts, daily_ideas_text

        out["daily_ideas_sent"] = daily_ideas_alerts(db)
        out["daily_ideas"] = daily_ideas_text(db)  # also in the run log
        if not out["daily_ideas_sent"]:
            out["daily_ideas_note"] = _why_no_digest(db)
    out["retry_notifications"] = run_job(db, "retry_notifications", retry_notifications).status
    out["retention"] = run_job(db, "retention", retention).status
    return out


def _why_no_digest(db: Session) -> str:
    """Plain reason the daily-ideas message was not sent (printed in the GitHub Actions log; no personal data)."""
    from sqlalchemy import func, select

    from app.models import Alert, ScanRun
    from app.services.scan_service import latest_run

    nse = latest_run(db, "NSE")
    if nse is None:
        runs = [(r.status, (r.stats or {}).get("is_sample")) for r in db.scalars(
            select(ScanRun).where(ScanRun.market == "NSE").order_by(ScanRun.id.desc()).limit(3))]
        return f"no NSE scan matching the current provider (latest runs: status, is_sample = {runs})"
    counts = dict(db.execute(select(Alert.status, func.count()).where(Alert.kind == "daily_ideas").group_by(Alert.status)).all())
    if not counts.get("active"):
        return f"no active daily_ideas alert (by status: {counts or 'none'}); set BOOTSTRAP_ADMIN_EMAIL/PASSWORD or add one in Alerts"
    sent = [a.last_bar for a in db.scalars(select(Alert).where(Alert.kind == "daily_ideas", Alert.status == "active"))]
    return f"already sent for the {nse.as_of} session (sent keys: {sent})"


def dispatch_github(market: str, full: bool = False) -> dict:
    """Start the GitHub Actions 'daily' workflow (used on Vercel, where a request cannot run for an hour)."""
    import httpx

    s = get_settings()
    if not (s.github_dispatch_token and s.github_repo):
        raise RuntimeError("Set GITHUB_REPO and GITHUB_DISPATCH_TOKEN to run jobs from the app (JOB_RUNNER=github)")
    r = httpx.post(f"https://api.github.com/repos/{s.github_repo}/actions/workflows/daily.yml/dispatches", timeout=20,
                   headers={"Authorization": f"Bearer {s.github_dispatch_token}", "Accept": "application/vnd.github+json"},
                   json={"ref": s.github_ref, "inputs": {"market": market, "full": str(bool(full)).lower()}})
    if r.status_code not in (200, 204):
        raise RuntimeError(f"GitHub refused the dispatch (HTTP {r.status_code}): {r.text[:200]}")
    return {"dispatched": True, "actions_url": f"https://github.com/{s.github_repo}/actions/workflows/daily.yml"}
