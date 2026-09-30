from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import AppSetting
from engine.config import EngineConfig

ENGINE_KEY = "engine_config"


def engine_overrides(db: Session) -> dict:
    row = db.get(AppSetting, ENGINE_KEY)
    return dict(row.value) if row else {}


def engine_config(db: Session, analysis_mode: Optional[str] = None, market: str = "NSE") -> EngineConfig:
    """Global admin overrides → market profile (thresholds in that market's currency, costs) →
    admin per-market overrides (`overrides["markets"][market]`)."""
    from engine.markets import profile

    ov = engine_overrides(db)
    if analysis_mode:
        ov = {**ov, "analysis_mode": analysis_mode}
    cfg = EngineConfig.from_overrides(ov)
    if market != "NSE":
        p = profile(market)
        for k, v in p.validation.items():
            setattr(cfg.validation, k, type(getattr(cfg.validation, k))(v))
        cfg.backtest.costs = type(cfg.backtest.costs)(p.costs.commission_pct, p.costs.slippage_pct)
        cfg.analysis_mode = "technical"  # no fundamentals feed outside NSE yet: score on technical components only
    per = (ov.get("markets") or {}).get(market)
    if per:
        cfg = EngineConfig.from_overrides({**cfg.to_dict(), **per, "weights": cfg.weights, "labels": cfg.labels})
    return cfg


def save_engine_overrides(db: Session, value: dict, user_id: Optional[int]) -> EngineConfig:
    cfg = EngineConfig.from_overrides(value)  # validates / coerces
    row = db.get(AppSetting, ENGINE_KEY) or AppSetting(key=ENGINE_KEY, value={})
    row.value, row.updated_by = value, user_id
    db.merge(row)
    db.commit()
    return cfg


# ---------------------------------------------------------------- per-market strategy switches
CONTROLS_KEY = "strategy_controls"


def strategy_controls(db: Session) -> dict:
    """{market: {strategy_id: {"reason", "by", "at"}}} — strategies switched OFF for live setups.
    Disabled strategies are still backtested every scan, so their track record keeps updating."""
    row = db.get(AppSetting, CONTROLS_KEY)
    return dict(row.value) if row else {}


def disabled_strategies(db: Session, market: str) -> dict:
    return dict(strategy_controls(db).get(market) or {})


def set_strategy_enabled(db: Session, market: str, strategy_id: str, enabled: bool, reason: str, user_id: Optional[int]) -> dict:
    from datetime import datetime, timezone

    ctl = strategy_controls(db)
    per = dict(ctl.get(market) or {})
    if enabled:
        per.pop(strategy_id, None)
    else:
        per[strategy_id] = {"reason": reason, "by": user_id, "at": datetime.now(timezone.utc).isoformat()}
    ctl[market] = per
    row = db.get(AppSetting, CONTROLS_KEY) or AppSetting(key=CONTROLS_KEY, value={})
    row.value, row.updated_by = ctl, user_id
    db.merge(row)
    db.commit()
    return per
