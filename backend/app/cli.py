"""Operational CLI (the GitHub Actions schedule runs `daily`).

    python -m app.cli daily NSE|CRYPTO [--full]   # the end-of-day pipeline: ingest → scan → (NSE) options + daily ideas message
    python -m app.cli create-admin EMAIL PASSWORD
    python -m app.cli ingest [--market NSE] [--full]
    python -m app.cli scan [--market NSE]
    python -m app.cli options                    # option chain + NIFTY options analysis
    python -m app.cli bootstrap-sample           # SAMPLE data for NSE + CRYPTO, then scan (development)
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


def ensure_daily_ideas_alert(db: Session, user: User) -> None:
    """The admin gets the daily trade-ideas message by default (one per session; editable in Alerts)."""
    from app.models import Alert

    if not db.scalar(select(Alert).where(Alert.user_id == user.id, Alert.kind == "daily_ideas")):
        db.add(Alert(user_id=user.id, kind="daily_ideas", params={}, repeat=True, channels=[], note=""))
        db.commit()


def main() -> None:
    p = argparse.ArgumentParser(prog="marketedge")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily")
    d.add_argument("market", choices=["NSE", "CRYPTO"])
    d.add_argument("--full", action="store_true")
    a = sub.add_parser("create-admin")
    a.add_argument("email")
    a.add_argument("password")
    i = sub.add_parser("ingest")
    i.add_argument("--full", action="store_true")
    i.add_argument("--market", default="NSE")
    sc = sub.add_parser("scan")
    sc.add_argument("--market", default="NSE")
    sub.add_parser("options")
    sub.add_parser("bootstrap-sample")
    args = p.parse_args()

    from app import jobs
    from app.core.markets import enabled_markets

    db = SessionLocal()
    try:
        seed_rbac(db)
        s = get_settings()
        if s.bootstrap_admin_email and s.bootstrap_admin_password:
            ensure_daily_ideas_alert(db, ensure_admin(db, s.bootstrap_admin_email, s.bootstrap_admin_password))
        if args.cmd == "daily":
            out = jobs.daily(db, args.market, full=args.full)
            print(json.dumps(out, indent=2, default=str))
            failed = [k for k, v in out.items() if v == "failed"]
            if failed:
                raise SystemExit(f"steps failed: {failed} (see the jobs table / Admin → Jobs)")
        elif args.cmd == "create-admin":
            ensure_admin(db, args.email, args.password)
            print(f"admin ready: {args.email}")
        elif args.cmd == "ingest":
            print(json.dumps(jobs.ingest(db, args.market, full=args.full), indent=2, default=str))
        elif args.cmd == "scan":
            print(json.dumps(jobs.scan(db, args.market), indent=2, default=str))
        elif args.cmd == "options":
            print(json.dumps(jobs.options(db), indent=2, default=str))
        elif args.cmd == "bootstrap-sample":
            if s.market_data_provider != "sample":
                raise SystemExit("bootstrap-sample requires MARKET_DATA_PROVIDER=sample")
            for m in enabled_markets():
                print(f"Ingesting SAMPLE {m} data…")
                print(json.dumps(jobs.ingest(db, m, full=True), default=str))
                print(json.dumps({"market": m, **jobs.scan(db, m)}, default=str))
            if "NSE" in enabled_markets():
                print(json.dumps(jobs.options(db), indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
