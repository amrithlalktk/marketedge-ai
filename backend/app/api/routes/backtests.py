from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ip_of, permissions_of, require
from app.core.db import get_db
from app.models import Backtest, BacktestTrade, Job, StrategyVersion, User
from app.schemas import BacktestIn
from app.services.audit import audit
from app.services.backtest_service import resolve_strategy
from app.workers import tasks
from engine.analyzer import DISCLAIMER
from engine.strategies import StrategyDefinitionError

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _version_no(db: Session, bt: Backtest):
    if bt.strategy_version_id is None:
        return None
    v = db.get(StrategyVersion, bt.strategy_version_id)
    return v.version if v else None


def _visible(bt: Backtest, user: User) -> bool:
    return bt.user_id == user.id or "admin:jobs" in permissions_of(user)


@router.post("", status_code=202)
def create(body: BacktestIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("backtests:run"))):
    if not body.strategy_key and not body.definition:
        raise HTTPException(422, "Provide strategy_key or definition")
    if body.start and body.end and body.start >= body.end:
        raise HTTPException(422, "start must be before end")
    params = body.model_dump(mode="json", exclude={"commission_pct", "slippage_pct"})
    params["costs"] = {k: v for k, v in (("commission_pct", body.commission_pct), ("slippage_pct", body.slippage_pct)) if v is not None}
    if body.definition:
        params["definition"] = body.definition.engine_dict() | {"id": "custom"}
    try:
        spec, version_id = resolve_strategy(db, params)
    except (ValueError, StrategyDefinitionError) as exc:
        raise HTTPException(422, str(exc)) from exc
    bt = Backtest(user_id=user.id, kind="user", strategy_key=spec.id, strategy_version_id=version_id, status="queued", params=params)
    db.add(bt)
    db.flush()
    job = Job(kind="backtest", params={"backtest_id": bt.id}, created_by=user.id)
    db.add(job)
    db.commit()
    audit(db, "backtest.create", user.id, str(bt.id), {"strategy": spec.id}, ip_of(request))
    res = tasks.backtest.delay(job.id, bt.id)
    job.task_id = getattr(res, "id", None)
    db.commit()
    return {"id": bt.id, "job_id": job.id, "status": db.get(Backtest, bt.id).status}


@router.get("")
def list_backtests(db: Session = Depends(get_db), user: User = Depends(require("backtests:run")), limit: int = Query(20, ge=1, le=100)):
    rows = db.scalars(select(Backtest).where(Backtest.kind == "user", Backtest.user_id == user.id).order_by(Backtest.id.desc()).limit(limit))
    return {"items": [{"id": b.id, "strategy_key": b.strategy_key, "strategy_version": _version_no(db, b),
                       "has_portfolio": bool((b.result or {}).get("portfolio")), "status": b.status, "created_at": b.created_at.isoformat(),
                       "finished_at": b.finished_at.isoformat() if b.finished_at else None,
                       "trades": ((b.result or {}).get("summary") or {}).get("sample_size"),
                       "expectancy_r": ((b.result or {}).get("summary") or {}).get("expectancy_r"), "error": b.error} for b in rows]}


@router.get("/{backtest_id}")
def get_backtest(backtest_id: int, db: Session = Depends(get_db), user: User = Depends(require("backtests:run"))):
    bt = db.get(Backtest, backtest_id)
    if bt is None or not _visible(bt, user):
        raise HTTPException(404, "Backtest not found")
    return {"id": bt.id, "status": bt.status, "strategy_version": _version_no(db, bt), "params": bt.params, "result": bt.result, "error": bt.error,
            "created_at": bt.created_at.isoformat(), "finished_at": bt.finished_at.isoformat() if bt.finished_at else None, "disclaimer": DISCLAIMER}


@router.get("/{backtest_id}/trades")
def trades(backtest_id: int, db: Session = Depends(get_db), user: User = Depends(require("backtests:run")),
           page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000)):
    bt = db.get(Backtest, backtest_id)
    if bt is None or not _visible(bt, user):
        raise HTTPException(404, "Backtest not found")
    q = select(BacktestTrade).where(BacktestTrade.backtest_id == backtest_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(BacktestTrade.signal_date.desc()).offset((page - 1) * page_size).limit(page_size))
    cols = [c.name for c in BacktestTrade.__table__.columns if c.name != "backtest_id"]
    return {"total": total, "page": page, "items": [{c: (str(getattr(r, c)) if c.endswith("_date") else getattr(r, c)) for c in cols} for r in rows]}


@router.get("/compare/summary")
def compare(ids: str = Query(..., pattern=r"^\d+(,\d+){1,4}$"), db: Session = Depends(get_db), user: User = Depends(require("backtests:run"))):
    """Side-by-side key metrics for 2–5 of the user's backtests."""
    out = []
    for i in [int(x) for x in ids.split(",")]:
        bt = db.get(Backtest, i)
        if bt is None or not _visible(bt, user) or bt.status != "done":
            raise HTTPException(404, f"Backtest {i} not found or not finished")
        r = bt.result or {}
        s, pf = r.get("summary", {}), r.get("portfolio") or {}
        oos = (r.get("segments") or {}).get("out_of_sample", {})
        out.append({"id": bt.id, "strategy": (r.get("strategy") or {}).get("name"), "strategy_key": bt.strategy_key,
                    "strategy_version_id": bt.strategy_version_id, "strategy_version": _version_no(db, bt), "period": r.get("period"), "universe_size": r.get("universe_size"),
                    "trades": s.get("sample_size"), "win_rate": s.get("win_rate"), "t1_hit_rate": s.get("t1_hit_rate"),
                    "stop_rate": s.get("stop_rate"), "profit_factor": s.get("profit_factor"), "expectancy_r": s.get("expectancy_r"),
                    "oos_expectancy_r": oos.get("expectancy_r"), "oos_trades": oos.get("sample_size"),
                    "wf_oos_expectancy_r": ((r.get("walk_forward") or {}).get("out_of_sample_combined") or {}).get("expectancy_r"),
                    "portfolio_cagr_pct": pf.get("cagr_pct"), "portfolio_max_dd_pct": pf.get("max_drawdown_pct"), "portfolio_sharpe": pf.get("sharpe"),
                    "trades_skipped": sum((pf.get("skipped") or {}).values()) if pf else None,
                    "positive_years": (r.get("yearly_stability") or {}).get("positive_years"),
                    "years_evaluated": (r.get("yearly_stability") or {}).get("years_evaluated"),
                    "warnings": r.get("warnings", []), "costs": r.get("costs")})
    return {"items": out, "note": "Compare out-of-sample and portfolio figures, not just in-sample totals.", "disclaimer": DISCLAIMER}


@router.get("/{backtest_id}/trades.csv")
def trades_csv(backtest_id: int, db: Session = Depends(get_db), user: User = Depends(require("backtests:run"))):
    import csv
    import io

    from fastapi.responses import StreamingResponse

    bt = db.get(Backtest, backtest_id)
    if bt is None or not _visible(bt, user):
        raise HTTPException(404, "Backtest not found")
    cols = [c.name for c in BacktestTrade.__table__.columns if c.name not in ("id", "backtest_id")]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in db.scalars(select(BacktestTrade).where(BacktestTrade.backtest_id == backtest_id).order_by(BacktestTrade.signal_date)):
        w.writerow([_csv_safe(getattr(r, c)) for c in cols])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="backtest_{backtest_id}_trades.csv"'})


def _csv_safe(v):
    """Neutralise spreadsheet formula injection in text cells."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v
