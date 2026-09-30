from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ip_of, require
from app.api.routes.markets import latest_snapshot
from app.core.db import get_db
from app.models import Strategy, User
from app.schemas import StrategyDefinition, StrategyIn, StrategyPatch, StrategyVersionIn
from app.services import strategy_service
from app.services.audit import audit
from engine.strategies import ALLOWED_FEATURES, STRATEGIES, StrategyDefinitionError, build_custom_strategy

router = APIRouter(prefix="/strategies", tags=["strategies"])
OPERATORS = [">", ">=", "<", "<=", "crosses_above", "crosses_below"]


def _perf_map(db: Session) -> dict:
    return {p["strategy"]["id"]: p for p in (latest_snapshot(db, "strategy_performance") or {}).get("strategies", [])}


def _perf_panel(p: dict):
    if not p:
        return None
    s = p.get("summary", {})
    return {"backtest_period": p.get("backtest_period"), "trades": s.get("sample_size"), "win_rate": s.get("win_rate"),
            "t1_hit_rate": s.get("t1_hit_rate"), "t2_hit_rate": s.get("t2_hit_rate"), "stop_rate": s.get("stop_rate"),
            "profit_factor": s.get("profit_factor"), "max_drawdown_pct": s.get("max_drawdown_pct"), "avg_holding_bars": s.get("avg_holding_bars"),
            "expectancy_r": s.get("expectancy_r"), "segments": p.get("segments"), "by_regime": p.get("by_regime"), "warnings": p.get("warnings"),
            "costs": p.get("costs")}


def _custom_out(r: Strategy, perf: dict) -> dict:
    spec = strategy_service.spec_for(r)
    return {**spec.public(), "id": r.key, "builtin": False, "definition": r.definition, "is_active": r.is_active,
            "include_in_scan": r.include_in_scan, "current_version": r.current_version, "owner_id": r.owner_id,
            "versions": [{"version": v.version, "note": v.note, "created_at": v.created_at.isoformat(), "created_by": v.created_by} for v in r.versions],
            "performance": _perf_panel(perf.get(r.key))}


def _row(db: Session, key: str) -> Strategy:
    row = db.scalar(select(Strategy).where(Strategy.key == key, Strategy.is_builtin.is_(False)))
    if row is None:
        raise HTTPException(404, "Strategy not found")
    return row


@router.get("", dependencies=[Depends(require("analysis:read"))])
def list_strategies(db: Session = Depends(get_db)):
    from app.services.settings_service import strategy_controls

    perf = _perf_map(db)
    ctl = strategy_controls(db)
    off = lambda sid: {m: v for m, per in ctl.items() for k, v in (per or {}).items() if k == sid}  # noqa: E731
    builtin = [{**spec.public(), "builtin": True, "performance": _perf_panel(perf.get(sid)), "disabled_markets": off(sid)}
               for sid, spec in STRATEGIES.items()]
    custom = [_custom_out(r, perf) for r in db.scalars(select(Strategy).where(Strategy.is_builtin.is_(False)).order_by(Strategy.id))]
    return {"builtin": builtin, "custom": custom, "allowed_features": sorted(ALLOWED_FEATURES), "operators": OPERATORS,
            "dsl": {"operand": "number | feature | {feature, mult (0.01–100), shift (0–20 bars ago)}",
                    "any_of": "up to 4 OR-groups; each group's conditions are ANDed",
                    "exits": {"stop_method": ["structure", "atr"], "atr_stop_mult": [0.5, 6], "t1_r": [0.5, 5], "t2_r": [1, 10], "max_hold_bars": [1, 120]}}}


@router.post("/validate", dependencies=[Depends(require("analysis:read"))])
def validate(body: StrategyDefinition):
    try:
        spec = build_custom_strategy(body.engine_dict())
    except StrategyDefinitionError as exc:
        return {"valid": False, "error": str(exc)}
    return {"valid": True, "rules": spec.conditions, "description": spec.description, "stop_rule": spec.stop_rule,
            "target_rule": spec.target_rule, "max_hold_bars": spec.max_hold_bars, "entry_rule": spec.entry_rule}


@router.get("/{key}", dependencies=[Depends(require("analysis:read"))])
def get_strategy(key: str, db: Session = Depends(get_db)):
    perf = _perf_map(db)
    if key in STRATEGIES:
        return {**STRATEGIES[key].public(), "builtin": True, "performance": _perf_panel(perf.get(key))}
    return _custom_out(_row(db, key), perf)


@router.get("/{key}/versions/{version}", dependencies=[Depends(require("analysis:read"))])
def get_version(key: str, version: int, db: Session = Depends(get_db)):
    row = _row(db, key)
    v = next((x for x in row.versions if x.version == version), None)
    if v is None:
        raise HTTPException(404, "Version not found")
    return {"key": key, "version": v.version, "definition": v.definition, "note": v.note, "created_at": v.created_at.isoformat()}


@router.get("/{key}/performance", dependencies=[Depends(require("analysis:read"))])
def performance(key: str, db: Session = Depends(get_db)):
    p = _perf_map(db).get(key)
    if p is None:
        raise HTTPException(404, "No performance computed for this strategy (custom strategies appear after they are included in a scan)")
    return p


@router.post("", status_code=201)
def create_strategy(body: StrategyIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("strategies:manage"))):
    if body.key in STRATEGIES or db.scalar(select(Strategy).where(Strategy.key == body.key)):
        raise HTTPException(409, "Strategy key already exists")
    defn = StrategyDefinition(**body.model_dump(exclude={"key", "note"})).engine_dict()
    try:
        row = strategy_service.create(db, body.key, defn, user.id, body.note)
    except StrategyDefinitionError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, "strategy.create", user.id, body.key, {"version": 1, "definition": defn}, ip_of(request))
    return _custom_out(row, {})


@router.put("/{key}")
def update_strategy(key: str, body: StrategyVersionIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require("strategies:manage"))):
    """Edits never mutate history: a new immutable version is created and becomes current."""
    row = _row(db, key)
    defn = StrategyDefinition(**body.model_dump(exclude={"note"})).engine_dict()
    try:
        v = strategy_service.new_version(db, row, defn, user.id, body.note)
    except StrategyDefinitionError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, "strategy.version", user.id, key, {"version": v.version, "definition": defn}, ip_of(request))
    return _custom_out(row, {})


@router.patch("/{key}")
def patch_strategy(key: str, body: StrategyPatch, request: Request, db: Session = Depends(get_db), user: User = Depends(require("strategies:manage"))):
    row = _row(db, key)
    if body.include_in_scan is not None:
        row.include_in_scan = body.include_in_scan
    if body.is_active is not None:
        row.is_active = body.is_active
        if not body.is_active:
            row.include_in_scan = False
    db.commit()
    audit(db, "strategy.update", user.id, key, body.model_dump(exclude_none=True), ip_of(request))
    return {"key": key, "include_in_scan": row.include_in_scan, "is_active": row.is_active,
            "note": "Scan inclusion takes effect from the next scan; its setups then carry the same historical evidence and NO TRADE checks as built-ins."}


@router.delete("/{key}", status_code=204)
def delete_strategy(key: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require("strategies:manage"))):
    row = _row(db, key)
    row.is_active, row.include_in_scan = False, False
    db.commit()
    audit(db, "strategy.deactivate", user.id, key, ip=ip_of(request))
