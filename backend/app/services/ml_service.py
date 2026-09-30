"""ML model lifecycle: train (worker) → review → activate (admin, gate required) → serve (scan) → drift-monitor."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import MLModel, Signal, SignalOutcome
from app.services.audit import audit
from app.services.market_data import instrument_maps, load_bars
from app.services.scan_service import latest_events, latest_run
from app.services.settings_service import engine_config
from engine import ENGINE_VERSION
from engine.analyzer import Analyzer
from engine.ml.dataset import build_dataset, feature_names, row_features
from engine.ml.model import TrainConfig, from_artifact, train_and_select
from engine.strategies import STRATEGIES, strategy_set

log = logging.getLogger(__name__)
DRIFT_MIN_RESOLVED = 50
DRIFT_TOLERANCE = 1.10  # suspend if live Brier > 110% of the empirical baseline's live Brier


def _strategy_ids(market: str) -> List[str]:
    from app.core.markets import market_config

    prof = market_config(market).profile
    return sorted(strategy_set(prof.strategy_set)) if prof.strategy_set != "equity" else sorted(STRATEGIES)


def train(db: Session, market: str, user_id: Optional[int] = None, cfg: Optional[TrainConfig] = None) -> MLModel:
    from app.core.markets import market_config

    run = latest_run(db, market)
    if run is None:
        raise RuntimeError(f"No completed scan for {market}; the training set is the scan's historical event set")
    events = latest_events(db, market)
    if events.empty:
        raise RuntimeError("Latest scan has no historical events")
    ids, info = instrument_maps(db, market)
    bars = load_bars(db, ids)
    mc = market_config(market)
    ec = engine_config(db, market=market)
    an = Analyzer(ec, workers=get_settings().scan_workers)
    feats = an.prepare({k: v for k, v in bars.items() if not info[k]["is_index"]})
    from app.services.universe_service import membership_for

    membership, _, _ = membership_for(db, market, info, bars)
    ctx = an.market_context(bars[mc.benchmark], feats, membership=membership)
    strats = _strategy_ids(market)
    events = events[events["strategy_id"].isin(strats)]
    ds = build_dataset(feats, events, strats, ctx.breadth_df["pct_above_50"] if not ctx.breadth_df.empty else None)
    if ds.empty:
        raise RuntimeError("Could not build a training set")
    res = train_and_select(ds, feature_names(strats), cfg)
    if res["selected"] is None:
        raise RuntimeError("Walk-forward evaluation produced no out-of-sample folds (too little history)")
    version = (db.scalar(select(func.max(MLModel.version)).where(MLModel.market == market)) or 0) + 1
    m = MLModel(market=market, algo=res["selected"], version=version, status="candidate", eligible=res["gate"]["eligible"],
                source_scan_run_id=run.id, metrics={k: res[k] for k in ("results", "gate", "config", "importance", "n_events", "period")},
                artifact=res["artifact"], features=feature_names(strats), engine_version=ENGINE_VERSION,
                is_sample_data=any(v["is_sample"] for v in info.values()), created_by=user_id)
    db.add(m)
    db.commit()
    audit(db, "ml.train", user_id, f"{market} v{version}", {"algo": m.algo, "eligible": m.eligible, "gate": res["gate"]})
    return m


def set_status(db: Session, model: MLModel, status: str, user_id: Optional[int], force_reason: Optional[str] = None) -> MLModel:
    if status == "active":
        oos = (model.metrics.get("results", {}).get(model.algo) or {}).get("oos") or {}
        if "significance" not in oos:
            raise ValueError("Model was trained before the statistical-significance gate existed; retrain it to re-evaluate")
        if not model.eligible:
            raise ValueError("Model did not pass the out-of-sample gate (" + "; ".join(model.metrics.get("gate", {}).get("reasons", [])) + ")")
        for other in db.scalars(select(MLModel).where(MLModel.market == model.market, MLModel.status == "active", MLModel.id != model.id)):
            other.status = "retired"
        model.activated_at = datetime.now(timezone.utc)
    model.status = status
    db.commit()
    audit(db, f"ml.{status}", user_id, f"{model.market} v{model.version}", {"reason": force_reason} if force_reason else {})
    return model


def active_model(db: Session, market: str) -> Optional[MLModel]:
    return db.scalar(select(MLModel).where(MLModel.market == market, MLModel.status == "active").limit(1))


def annotate_setups(db: Session, market: str, setups: List[dict], feats: Dict[str, pd.DataFrame], breadth50: Optional[pd.Series],
                    weights: Dict[str, float]) -> Optional[dict]:
    """Attach the calibrated ML probability to each setup (next to the empirical rate). Returns model summary."""
    from engine.scoring import combine

    m = active_model(db, market)
    if m is None:
        for st in setups:
            st["ml"] = {"available": False, "note": "No validated ML model is active for this market; the empirical hit rate is the only estimate."}
        return None
    model = from_artifact(m.artifact)
    strats = [f.split("=", 1)[1] for f in m.features if f.startswith("strategy=")]
    oos = (m.metrics.get("results", {}).get(m.algo) or {}).get("oos") or {}
    for st in setups:
        f = feats.get(st["symbol"])
        if f is None:
            continue
        row = f.iloc[-1]
        b = float(breadth50.iloc[-1]) if breadth50 is not None and len(breadth50) else np.nan
        x = row_features(row, st["direction"], (st.get("market_regime") or {}).get("regime"), st["strategy"]["id"], st["rr_t2"],
                         st["score"], b, strats)
        if st["strategy"]["id"] not in strats:
            st["ml"] = {"available": False, "note": "The active model was not trained on this strategy."}
            continue
        p = float(model.predict(pd.DataFrame([x]))[0])
        st["ml"] = {"available": True, "probability_t1_pct": round(100 * p, 1), "model_id": m.id, "version": m.version, "algo": m.algo,
                    "oos_brier": oos.get("model", {}).get("brier"), "baseline_brier": oos.get("baseline", {}).get("brier"),
                    "oos_auc": oos.get("model", {}).get("auc"), "oos_n": oos.get("model", {}).get("n"), "calibrated": bool(m.artifact.get("isotonic")),
                    "empirical_t1_pct": (st.get("probability") or {}).get("t1_hit_rate"),
                    "note": ("Calibrated model estimate of T1 before stop, validated out of sample against the empirical rate. "
                             "It is shown next to — not instead of — the historical hit rate, and is not a guarantee.")}
        st["components"]["ml"] = round(100 * p, 1)
        if weights.get("ml", 0) > 0:
            st["score"] = combine(st["components"], weights)
    return {"model_id": m.id, "version": m.version, "algo": m.algo}


def check_drift(db: Session, market: str) -> Optional[dict]:
    """Compare live Brier of the active model with the empirical estimate on resolved published setups."""
    m = active_model(db, market)
    if m is None:
        return None
    rows = db.execute(select(Signal.payload, SignalOutcome.t1_hit).join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
                      .where(Signal.market == market, SignalOutcome.status == "resolved")).all()
    pairs = [((p.get("ml") or {}).get("probability_t1_pct"), (p.get("probability") or {}).get("t1_hit_rate"), bool(y))
             for p, y in rows if (p.get("ml") or {}).get("model_id") == m.id]
    pairs = [(a / 100, b / 100, y) for a, b, y in pairs if a is not None and b is not None]
    out = {"model_id": m.id, "resolved": len(pairs)}
    if len(pairs) < DRIFT_MIN_RESOLVED:
        return {**out, "status": "insufficient live outcomes"}
    y = np.array([p[2] for p in pairs], dtype=float)
    ml_b = float(np.mean((np.array([p[0] for p in pairs]) - y) ** 2))
    emp_b = float(np.mean((np.array([p[1] for p in pairs]) - y) ** 2))
    out.update({"live_brier_model": round(ml_b, 5), "live_brier_empirical": round(emp_b, 5)})
    if ml_b > emp_b * DRIFT_TOLERANCE:
        set_status(db, m, "suspended", None, f"Live Brier {ml_b:.4f} worse than empirical {emp_b:.4f} over {len(pairs)} resolved setups")
        out["action"] = "suspended"
    return out
