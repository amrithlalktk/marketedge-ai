"""Operational CLI.

    python -m app.cli create-admin EMAIL PASSWORD
    python -m app.cli ingest [--market NSE] [--full]
    python -m app.cli scan [--market NSE]
    python -m app.cli options               # ingest the option chain + run NIFTY options analysis
    python -m app.cli news [--market NSE]   # fetch news + classify sentiment
    python -m app.cli calendar              # earnings + economic calendars
    python -m app.cli ml-train [--market NSE]  # train + walk-forward-validate an ML candidate (activation is an admin decision)
    python -m app.cli bootstrap-sample      # seed roles, ingest SAMPLE data for every market, scan everything
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.rbac import seed_rbac
from app.core.security import hash_password, validate_password_strength
from app.models import Role, User


def ensure_admin(db: Session, email: str, password: str) -> User:
    problem = validate_password_strength(password)
    if problem:
        raise SystemExit(problem)
    role = db.scalar(select(Role).where(Role.name == "admin"))
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        user = User(email=email.lower(), password_hash=hash_password(password), full_name="Administrator", role_id=role.id)
        db.add(user)
    else:
        user.role_id = role.id
    db.commit()
    return user


def main() -> None:
    p = argparse.ArgumentParser(prog="marketedge")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin")
    a.add_argument("email")
    a.add_argument("password")
    i = sub.add_parser("ingest")
    i.add_argument("--full", action="store_true")
    i.add_argument("--market", default="NSE")
    sc = sub.add_parser("scan")
    sc.add_argument("--market", default="NSE")
    sub.add_parser("options")
    nw = sub.add_parser("news")
    nw.add_argument("--market", default="NSE")
    sub.add_parser("calendar")
    mt = sub.add_parser("ml-train")
    mt.add_argument("--market", default="NSE")
    sub.add_parser("bootstrap-sample")
    args = p.parse_args()

    from app.core.markets import enabled_markets
    from app.providers.registry import market_provider
    from app.services.events_service import ingest_calendar, ingest_news
    from app.services.market_data import ingest
    from app.services.options_service import ingest_chain, run_options
    from app.services.scan_service import run_scan

    db = SessionLocal()
    try:
        seed_rbac(db)
        s = get_settings()
        if args.cmd == "create-admin":
            ensure_admin(db, args.email, args.password)
            print(f"admin ready: {args.email}")
        elif args.cmd == "ingest":
            print(json.dumps(ingest(db, market_provider(market=args.market), args.market, full=args.full), indent=2))
        elif args.cmd == "scan":
            run = run_scan(db, args.market)
            print(json.dumps({"scan_run_id": run.id, **run.stats}, indent=2, default=str))
        elif args.cmd == "news":
            print(json.dumps(ingest_news(db, args.market), default=str))
        elif args.cmd == "calendar":
            print(json.dumps(ingest_calendar(db, enabled_markets()), default=str))
        elif args.cmd == "ml-train":
            from app.services.ml_service import train

            m = train(db, args.market)
            print(json.dumps({"model_id": m.id, "version": m.version, "algo": m.algo, "eligible": m.eligible, "gate": m.metrics["gate"],
                              "oos": (m.metrics["results"][m.algo]["oos"] or {}).get("model"),
                              "baseline": (m.metrics["results"][m.algo]["oos"] or {}).get("baseline")}, indent=2, default=str))
        elif args.cmd == "options":
            print(json.dumps(ingest_chain(db), indent=2, default=str))
            run = run_options(db)
            print(json.dumps({"scan_run_id": run.id, **run.stats}, indent=2, default=str))
        elif args.cmd == "bootstrap-sample":
            if s.market_data_provider != "sample":
                raise SystemExit("bootstrap-sample requires MARKET_DATA_PROVIDER=sample")
            markets = enabled_markets()
            # FX first so every other market can convert to INR; NSE instruments before the calendar/news that reference them
            order = [m for m in ["FX", "NSE", "CRYPTO", "US", "EUROPE", "ASIA"] if m in markets]
            for m in order:
                print(f"Ingesting SAMPLE {m} data…")
                print(json.dumps(ingest(db, market_provider(market=m), m, full=True), default=str))
            print("Ingesting SAMPLE calendars and news…")
            print(json.dumps(ingest_calendar(db, markets), default=str))
            for m in order:
                print(json.dumps(ingest_news(db, m), default=str))
            for m in order:
                run = run_scan(db, m)
                print(json.dumps({"market": m, "scan_run_id": run.id, "valid": run.stats.get("valid"), "no_trade": run.stats.get("no_trade"),
                                  "events": run.stats.get("events")}, default=str))
            if "NSE" in markets:
                print("Ingesting SAMPLE option chain and running options analysis…")
                print(json.dumps(ingest_chain(db), default=str))
                run = run_options(db)
                print(json.dumps({"scan_run_id": run.id, **run.stats}, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
