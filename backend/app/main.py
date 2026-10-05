from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, alerts, auth, markets, notifications, options, portfolios, risk, signals, stocks, upstox, watchlists
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.core.observability import RequestContextMiddleware, configure_logging, init_sentry, readiness
from app.core.rbac import seed_rbac
from engine import ENGINE_VERSION

configure_logging()
init_sentry("api")


_SEED_LOCK = 727_401  # arbitrary advisory-lock key


def _seed() -> None:
    """Idempotent seeding. Every uvicorn worker / replica runs it at start-up, so on Postgres it is
    serialised with an advisory lock (otherwise concurrent first boots race on unique keys)."""
    from contextlib import nullcontext

    from sqlalchemy import text

    from app.core.db import engine

    pg = engine.dialect.name == "postgresql"
    try:  # an unreachable / unconfigured database must not take the whole app down at start-up
        with (engine.connect() if pg else nullcontext()) as lock_conn:
            if pg:
                lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SEED_LOCK})
            try:
                _seed_locked()
            finally:
                if pg:
                    lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SEED_LOCK})
    except Exception as exc:
        logging.getLogger(__name__).error("start-up seeding skipped (database unavailable): %s", type(exc).__name__)


def _seed_locked() -> None:
    s = get_settings()
    db = SessionLocal()
    try:
        seed_rbac(db)
        if s.bootstrap_admin_email and s.bootstrap_admin_password:
            from app.cli import ensure_admin

            ensure_admin(db, s.bootstrap_admin_email, s.bootstrap_admin_password)
    except Exception:  # tables may not exist before `alembic upgrade head`
        logging.getLogger(__name__).exception("startup seeding skipped")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _seed()
    yield


def _misconfigured_app(reason: str) -> FastAPI:
    logging.getLogger(__name__).error("refusing to serve: %s", reason)
    bad = FastAPI(title="MarketEdge AI API (misconfigured)", docs_url=None, redoc_url=None, openapi_url=None)

    @bad.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def misconfigured(path: str):
        return JSONResponse({"detail": f"Server misconfigured: {reason}"}, status_code=503)

    return bad


def create_app() -> FastAPI:
    s = get_settings()
    try:
        s.validate_production()
    except RuntimeError as exc:  # serve a clear 503 instead of crashing every invocation (the message names settings, never values)
        return _misconfigured_app(str(exc))
    app = FastAPI(
        lifespan=lifespan,
        title=f"{s.app_name} API",
        version=ENGINE_VERSION,
        description=("Analysis/research API. Historical and backtested statistics do not guarantee future results. "
                     "Every setup carries its data timestamp, sample size and methodology."),
        docs_url="/docs" if s.environment != "production" else None,
        redoc_url="/redoc" if s.environment != "production" else None,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
                       expose_headers=["X-Request-ID"])
    app.add_middleware(RequestContextMiddleware)  # outermost: request id + access log + HTTP metrics
    for r in (auth, markets, stocks, signals, watchlists, risk, options, portfolios, alerts, notifications, admin, upstox):
        app.include_router(r.router, prefix=s.api_prefix)
    app.include_router(signals.market_router, prefix=s.api_prefix)

    @app.get("/health", tags=["meta"])
    def health():
        """Liveness: the process is up. Does not touch dependencies (a DB outage must not restart every pod)."""
        return {"status": "ok", "engine_version": ENGINE_VERSION}

    @app.get("/ready", tags=["meta"])
    def ready():
        """Readiness: database reachable, migrations at head, Redis reachable."""
        r = readiness()
        return JSONResponse(r, status_code=200 if r["ready"] else 503)

    return app


app = create_app()
