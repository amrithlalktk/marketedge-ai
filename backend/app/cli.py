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


def options_report(db: Session) -> dict:
    """Read-only: why the latest NIFTY options scan did (not) publish an idea."""
    from app.models import MarketSnapshot

    snap = db.scalar(select(MarketSnapshot).where(MarketSnapshot.market == "NFO", MarketSnapshot.kind == "options").order_by(MarketSnapshot.id.desc()))
    if snap is None:
        return {"options": "no options scan stored yet"}
    o = snap.payload or {}

    def failed(checks):
        return [f"{c['name']}: {c.get('detail', '')}" for c in checks or [] if not c["passed"] and c["severity"] == "block"]

    from app.services.scan_service import latest_run

    run = latest_run(db, "NFO")
    return {"as_of": str(snap.as_of), "status": o.get("status"), "market_message": o.get("market_message"),
            "validation": {k: v.get("status") for k, v in ((run.stats or {}).get("validation") or {}).items()} if run else None,
            "spot": (o.get("underlying") or {}).get("spot"), "market_state": (o.get("market_state") or {}).get("direction"),
            "iv_percentile": (o.get("iv") or {}).get("iv_percentile"),
            "nifty_setups": [{"strategy": u["strategy"]["name"], "direction": u["direction"], "status": u["status"], "failed": failed(u.get("checks"))}
                             for u in o.get("underlying_setups") or []],
            "option_setups": [{"contract": (x.get("contract") or {}).get("label"), "status": x["status"], "reason": x.get("reason"),
                               "failed": failed(x.get("checks"))} for x in o.get("option_setups") or []]}


def stocks_report(db: Session, market: str = "NSE") -> dict:
    """Read-only: what the latest scan found and which checks rejected the candidates (most common first)."""
    from collections import Counter

    from app.models import Signal
    from app.services.scan_service import latest_run

    run = latest_run(db, market)
    if run is None:
        return {"scan": "none yet"}
    sigs = list(db.scalars(select(Signal).where(Signal.scan_run_id == run.id)))
    failed = Counter(c["name"] for sg in sigs if sg.status == "NO_TRADE" for c in (sg.payload or {}).get("checks", [])
                     if not c["passed"] and c["severity"] == "block")
    return {"as_of": run.as_of, "candidates": len(sigs), "valid": sum(sg.status == "VALID" for sg in sigs),
            "paper": sum(bool((sg.payload or {}).get("paper_trade")) for sg in sigs),
            "validated_strategies": (run.stats or {}).get("validated_strategies"),
            "regime": ((run.stats or {}).get("regime") or {}), "market_message": (run.stats or {}).get("market_message"),
            "rejected_by": dict(failed.most_common(10)),
            "closest": [{"symbol": sg.symbol, "strategy": sg.strategy_key, "direction": sg.direction, "score": round(sg.score, 1),
                         "failed": [c["name"] + ": " + c.get("detail", "") for c in (sg.payload or {}).get("checks", [])
                                    if not c["passed"] and c["severity"] == "block"]}
                        for sg in sorted(sigs, key=lambda x: -x.score)[:5]]}


def storage_report(db: Session) -> dict:
    """Read-only: database size, the largest tables and the date range of the main history tables (Postgres only)."""
    from sqlalchemy import text

    if db.bind.dialect.name != "postgresql":
        return {"skipped": "not Postgres"}
    q = lambda sql: db.execute(text(sql)).all()  # noqa: E731
    out = {"database_mb": round(q("SELECT pg_database_size(current_database())")[0][0] / 2**20, 1),
           "tables_mb": {r[0]: round(r[1] / 2**20, 1) for r in q(
               "SELECT relname, pg_total_relation_size(relid) FROM pg_statio_user_tables ORDER BY 2 DESC LIMIT 12")}}
    from sqlalchemy import func

    from app.models import MarketBar, MarketSnapshot, ScanRun, Signal, SignalOutcome

    for name, col in (("prices", MarketBar.ts), ("signals", Signal.as_of), ("snapshots", MarketSnapshot.as_of), ("scan_runs", ScanRun.started_at)):
        n, lo, hi = db.execute(select(func.count(), func.min(col), func.max(col))).one()
        out[name] = {"rows": n, "from": lo, "to": hi}
    out["signals_by_status"] = dict(db.execute(select(Signal.status, func.count()).group_by(Signal.status)).all())
    out["tracked_ideas"] = db.scalar(select(func.count()).select_from(SignalOutcome))
    out["scan_runs_by_market"] = dict(db.execute(select(ScanRun.market, func.count()).group_by(ScanRun.market)).all())
    return out


def main() -> None:
    p = argparse.ArgumentParser(prog="marketedge")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily")
    d.add_argument("market", choices=["NSE", "CRYPTO"])
    d.add_argument("--full", action="store_true")
    d.add_argument("--if-not-done", action="store_true", help="skip when this market already completed a scan in the last 12 hours "
                   "(the late GitHub schedule after the on-time Vercel Cron run)")
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
    sub.add_parser("report")
    dg = sub.add_parser("diagnose")
    dg.add_argument("--market", default="NSE")
    args = p.parse_args()

    from app import jobs
    from app.core.markets import enabled_markets

    db = SessionLocal()
    try:
        seed_rbac(db)
        s = get_settings()
        if s.bootstrap_admin_email and s.bootstrap_admin_password:
            ensure_daily_ideas_alert(db, ensure_admin(db, s.bootstrap_admin_email, s.bootstrap_admin_password))
        if args.cmd == "daily" and args.if_not_done and jobs.recently_done(db, args.market):
            print(json.dumps({"skipped": f"{args.market} already ran in the last 12 hours"}))
        elif args.cmd == "daily":
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
        elif args.cmd == "diagnose":
            from app.services.diagnostics_service import report as diagnose

            print(json.dumps(diagnose(db, args.market, hypotheses=True), indent=1, default=str))
        elif args.cmd == "report":
            print(json.dumps({"options": options_report(db), "stocks": stocks_report(db), "storage": storage_report(db)}, indent=2, default=str))
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
