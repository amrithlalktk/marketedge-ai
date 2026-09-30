"""Logging, request context, Prometheus metrics, readiness and optional Sentry.

* LOG_FORMAT=json → one JSON object per line (timestamp, level, logger, message, request_id, …).
* Every request gets an X-Request-ID (a client-supplied one is kept if it looks safe) that is
  echoed in the response header and attached to every log line of that request.
* /metrics (Prometheus text format) is protected by METRICS_TOKEN and disabled in production
  when no token is set. API-process metrics use prometheus_client multiprocess mode
  (PROMETHEUS_MULTIPROC_DIR) so every uvicorn worker is counted; job/scan/data/notification
  gauges are computed from the database at scrape time, so Celery workers need no exporter.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .config import get_settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
log = logging.getLogger("marketedge.access")


# ------------------------------------------------------------------ logging
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(), "level": record.levelname, "logger": record.name,
               "message": record.getMessage(), "request_id": request_id_var.get()}
        for k in ("method", "path", "route", "status", "duration_ms", "client_ip", "task", "job_id"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get()
        return True


def configure_logging() -> None:
    fmt = os.environ.get("LOG_FORMAT", "text")
    level = os.environ.get("LOG_LEVEL", "INFO")
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else
                         logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


# ------------------------------------------------------------------ metrics
try:
    from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
    from prometheus_client.core import GaugeMetricFamily

    _MULTI = bool(os.environ.get("PROMETHEUS_MULTIPROC_DIR"))
    HTTP_REQUESTS = Counter("marketedge_http_requests_total", "HTTP requests", ["method", "route", "status"])
    HTTP_LATENCY = Histogram("marketedge_http_request_duration_seconds", "HTTP request latency", ["method", "route"],
                             buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
    PROM = True
except ImportError:  # pragma: no cover
    PROM = False


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            dur = time.perf_counter() - start
            route = getattr(request.scope.get("route"), "path", None) or ("unmatched" if status == 404 else request.url.path)
            if PROM and not route.endswith("/metrics"):
                HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
                HTTP_LATENCY.labels(request.method, route).observe(dur)
            if not route.endswith(("/health", "/metrics", "/ready")):
                log.info("%s %s %s %.1fms", request.method, route, status, dur * 1000,
                         extra={"method": request.method, "path": request.url.path, "route": route, "status": status,
                                "duration_ms": round(dur * 1000, 1), "client_ip": request.client.host if request.client else None})
            request_id_var.reset(token)


class DbCollector:
    """Business/ops gauges computed from the database at scrape time (covers Celery workers too)."""

    def collect(self):
        from sqlalchemy import func, select

        from app.core.db import SessionLocal
        from app.core.markets import enabled_markets, market_config
        from app.models import DataStatus, Instrument, Job, MLModel, Notification, ScanRun
        from engine.validation import expected_last_session

        db = SessionLocal()
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            jobs = GaugeMetricFamily("marketedge_jobs_24h", "Background jobs in the last 24h", labels=["kind", "status"])
            for kind, status, n in db.execute(select(Job.kind, Job.status, func.count()).where(Job.created_at >= since).group_by(Job.kind, Job.status)):
                jobs.add_metric([kind, status], n)
            yield jobs
            valid = GaugeMetricFamily("marketedge_scan_valid_setups", "VALID setups in the latest scan", labels=["market"])
            age = GaugeMetricFamily("marketedge_scan_age_hours", "Hours since the latest successful scan", labels=["market"])
            failed = GaugeMetricFamily("marketedge_scan_last_failed", "1 if the most recent scan attempt failed", labels=["market"])
            lag = GaugeMetricFamily("marketedge_data_lag_days", "Max data lag (days) across a market's instruments", labels=["market"])
            ml = GaugeMetricFamily("marketedge_ml_active_models", "Active ML models", labels=["market"])
            today = datetime.now(timezone.utc).date()
            for m in enabled_markets() + ["NFO"]:
                last = db.scalar(select(ScanRun).where(ScanRun.market == m).order_by(ScanRun.id.desc()).limit(1))
                ok = db.scalar(select(ScanRun).where(ScanRun.market == m, ScanRun.status == "done").order_by(ScanRun.id.desc()).limit(1))
                if last is not None:
                    failed.add_metric([m], 1.0 if last.status == "failed" else 0.0)
                if ok is not None:
                    valid.add_metric([m], float((ok.stats or {}).get("valid", 0) or 0))
                    fin = ok.finished_at if ok.finished_at.tzinfo else ok.finished_at.replace(tzinfo=timezone.utc)
                    age.add_metric([m], (datetime.now(timezone.utc) - fin).total_seconds() / 3600)
                if m == "NFO":
                    continue
                last_bar = db.scalar(select(func.min(DataStatus.last_bar_ts)).join(Instrument, Instrument.id == DataStatus.instrument_id)
                                     .where(Instrument.market == m, DataStatus.interval == "1d", Instrument.is_active.is_(True),
                                            Instrument.delisted_on.is_(None)))
                if last_bar is not None:
                    exp = expected_last_session(today, calendar=market_config(m).profile.calendar)
                    lag.add_metric([m], float((exp - last_bar.date()).days))
                ml.add_metric([m], float(db.scalar(select(func.count()).select_from(MLModel).where(MLModel.market == m, MLModel.status == "active")) or 0))
            for g in (valid, age, failed, lag, ml):
                yield g
            deliveries = GaugeMetricFamily("marketedge_notification_deliveries_24h", "Notification deliveries in the last 24h", labels=["channel", "status"])
            counts = {}
            for (d,) in db.execute(select(Notification.deliveries).where(Notification.created_at >= since)):
                for ch, v in (d or {}).items():
                    counts[(ch, v.get("status", "?"))] = counts.get((ch, v.get("status", "?")), 0) + 1
            for (ch, st), n in counts.items():
                deliveries.add_metric([ch, st], n)
            yield deliveries
        finally:
            db.close()


QUEUES = ("default", "notifications", "scans", "backtests")


def queue_depths() -> dict:
    """Messages waiting per Celery queue (Redis broker: one list per queue). {} without Redis."""
    s = get_settings()
    if not s.broker_url or not s.broker_url.startswith(("redis://", "rediss://")):
        return {}
    try:
        import redis

        r = redis.Redis.from_url(s.broker_url, socket_connect_timeout=1, socket_timeout=2)
        return {q: int(r.llen(q)) for q in QUEUES}
    except Exception:
        return {}


class QueueCollector:
    def collect(self):
        g = GaugeMetricFamily("marketedge_queue_depth", "Messages waiting per Celery queue", labels=["queue"])
        for q, n in queue_depths().items():
            g.add_metric([q], n)
        yield g


def metrics_payload() -> tuple:
    if not PROM:
        return b"# prometheus_client not installed\n", "text/plain"
    db_reg = CollectorRegistry()
    db_reg.register(DbCollector())
    db_reg.register(QueueCollector())
    if _MULTI:
        from prometheus_client import multiprocess

        http_reg = CollectorRegistry()
        multiprocess.MultiProcessCollector(http_reg)
    else:
        from prometheus_client import REGISTRY as http_reg
    return generate_latest(http_reg) + generate_latest(db_reg), CONTENT_TYPE_LATEST


def metrics_allowed(authorization: str) -> bool:
    s = get_settings()
    token = s.metrics_token
    if not token:
        return s.environment != "production"
    import hmac

    return hmac.compare_digest(authorization or "", f"Bearer {token}")


# ------------------------------------------------------------------ readiness
def readiness() -> dict:
    from sqlalchemy import text

    from app.core.cache import get_cache
    from app.core.db import SessionLocal

    checks = {}
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
        try:
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            head = ScriptDirectory.from_config(Config(os.path.join(here, "alembic.ini"))).get_current_head()
            cur = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            checks["migrations"] = "ok" if cur == head else f"pending (db {cur}, code {head})"
        except Exception as exc:
            checks["migrations"] = f"unknown ({type(exc).__name__})"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    finally:
        db.close()
    s = get_settings()
    if s.redis_url:
        checks["redis"] = "ok" if get_cache().ping() else "unreachable"
    # "unknown" migrations (alembic metadata unreadable) does not block traffic; "pending" does
    ready = (checks.get("database") == "ok" and not checks.get("migrations", "ok").startswith("pending")
             and checks.get("redis", "ok") == "ok")
    return {"ready": bool(ready), "checks": checks}


# ------------------------------------------------------------------ sentry
def init_sentry(component: str) -> bool:
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger(__name__).warning("SENTRY_DSN set but sentry-sdk is not installed")
        return False
    sentry_sdk.init(dsn=dsn, environment=get_settings().environment, send_default_pii=False,
                    traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0")), server_name=component)
    return True
