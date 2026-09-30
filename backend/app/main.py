from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, alerts, analyst, analytics, auth, ml, news, notifications, portfolios, backtests, markets, options, risk, signals, stocks, strategies, upstox, watchlists
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.core.observability import RequestContextMiddleware, configure_logging, init_sentry, metrics_allowed, metrics_payload, readiness
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
    with (engine.connect() if pg else nullcontext()) as lock_conn:
        if pg:
            lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SEED_LOCK})
        try:
            _seed_locked()
        finally:
            if pg:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SEED_LOCK})


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


def create_app() -> FastAPI:
    s = get_settings()
    s.validate_production()
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
    for r in (auth, markets, stocks, signals, strategies, backtests, watchlists, risk, options, analytics, news, analyst, ml, portfolios, alerts, notifications, admin, upstox):
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

    @app.get("/metrics", tags=["meta"], include_in_schema=False)
    def metrics(authorization: str = Header(default="")):
        if not metrics_allowed(authorization):
            raise HTTPException(status_code=404)  # indistinguishable from "no such endpoint"
        body, ctype = metrics_payload()
        return Response(body, media_type=ctype)

    return app


app = create_app()
