"""Logging, request context, readiness and optional Sentry.

* LOG_FORMAT=json → one JSON object per line (timestamp, level, logger, message, request_id, …).
* Every request gets an X-Request-ID (a client-supplied one is kept if it looks safe) that is
  echoed in the response header and attached to every log line of that request.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

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
            if not route.endswith(("/health", "/metrics", "/ready")):
                log.info("%s %s %s %.1fms", request.method, route, status, dur * 1000,
                         extra={"method": request.method, "path": request.url.path, "route": route, "status": status,
                                "duration_ms": round(dur * 1000, 1), "client_ip": request.client.host if request.client else None})
            request_id_var.reset(token)


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
    if s.redis_url:  # optional shared cache
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
