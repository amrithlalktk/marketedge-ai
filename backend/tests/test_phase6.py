"""Phase 6: geometric patterns, ML dataset/validation/calibration/gate/persistence, ML regime,
ensemble components, model lifecycle API, scan annotation and drift suspension."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.features import build_features
from engine.ml.model import (Candidate, TrainConfig, baseline_predict, fold_windows, from_artifact, gate, split, to_artifact,
                             train_and_select)

API = "/api/v1"


# ------------------------------------------------------------------ patterns
def _ohlc(close, vol=1e6, start="2023-01-02"):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": close, "high": close + 0.3, "low": close - 0.3, "close": close, "volume": vol},
                        index=pd.bdate_range(start, periods=len(close)))


def test_ascending_triangle_breakout_detected_without_look_ahead():
    rng = np.random.default_rng(0)
    x = np.arange(200)
    lows = np.linspace(95, 108, 200)
    close = np.where(np.sin(x / 6) > 0, lows + (110 - lows) * np.sin(x / 6), lows + (110 - lows) * 0.1)
    close = np.concatenate([100 + rng.normal(0, 0.3, 60), close, np.linspace(110.5, 118, 10)])
    f = build_features(_ohlc(close))
    assert f["pat_triangle_asc"].sum() > 20
    brk = f.index[f["pat_geo_breakout_up"]]
    assert any(d >= f.index[260] for d in brk)  # breakout of the final flat top
    cut = 255
    part = build_features(_ohlc(close[: cut + 1]))
    for c in ("pat_triangle_asc", "pat_rectangle", "pat_geo_breakout_up", "pat_cup_handle"):
        assert bool(f[c].iloc[cut]) == bool(part[c].iloc[cut]), c


def test_cup_and_handle_detected():
    rng = np.random.default_rng(3)
    base = np.linspace(80, 99, 60)                                   # advance into the left rim
    left_rim = np.array([99.5, 100.0, 99.4])                          # a real (unique) peak
    cup = 100 - 22 * np.sin(np.linspace(0.05, np.pi - 0.05, 80))     # rounded ~22% cup
    right_rim = np.array([99.2, 99.8, 99.1])
    handle = np.linspace(98.6, 95.5, 6).tolist() + np.linspace(95.8, 99.3, 6).tolist()  # shallow handle (< 1/3 depth)
    brk = np.linspace(100.6, 105, 5)
    close = np.concatenate([base, left_rim, cup, right_rim, handle, brk]) + rng.normal(0, 0.05, 60 + 3 + 80 + 3 + 12 + 5)
    f = build_features(_ohlc(close, vol=1e6, start="2022-01-03"))
    assert f["pat_cup_handle"].any()
    assert f["pat_cup_handle_breakout"].iloc[-6:].any()


# ------------------------------------------------------------------ ML: synthetic dataset with a real signal
def _synthetic_ds(n=3000, signal=1.2, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2016-01-01", periods=n)
    x1, x2 = rng.normal(size=n), rng.normal(size=n)
    logit = -0.7 + signal * x1
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return pd.DataFrame({"x1": x1, "x2": x2, "y": y, "signal_date": dates, "exit_date": dates + pd.Timedelta(days=12),
                         "strategy_id": rng.choice(["a", "b"], n), "regime_family": rng.choice(["bull", "bear"], n), "r_multiple": 0.0})


CFG = TrainConfig(min_train=200, min_oos=200)


def test_purged_splits_never_leak_labels():
    ds = _synthetic_ds()
    emb = pd.Timedelta(days=CFG.embargo_days)
    for w in fold_windows(ds["signal_date"], CFG):
        train, calib, test = split(ds, w, emb)
        assert train["exit_date"].max() < w["calib_start"] - emb + pd.Timedelta(days=1)
        assert calib.empty or calib["exit_date"].max() < w["test_start"] - emb + pd.Timedelta(days=1)
        assert test["signal_date"].min() >= w["test_start"] and train["exit_date"].max() < test["signal_date"].min()


def test_model_with_signal_passes_gate_noise_does_not():
    good = train_and_select(_synthetic_ds(signal=1.2), ["x1", "x2"], CFG)
    assert good["gate"]["eligible"], good["gate"]
    oos = good["results"][good["selected"]]["oos"]
    assert oos["model"]["brier"] < oos["baseline"]["brier"] and oos["model"]["auc"] > 0.65
    assert good["importance"][0]["feature"] == "x1"
    noise = train_and_select(_synthetic_ds(signal=0.0), ["x1", "x2"], CFG)
    assert not noise["gate"]["eligible"]


def test_isotonic_calibration_fixes_a_biased_model():
    ds = _synthetic_ds(signal=1.0)
    train, cal, test = ds.iloc[:1500], ds.iloc[1500:2200], ds.iloc[2200:]
    m = Candidate("logistic", ["x1"]).fit(train, train["y"].to_numpy())
    m.est.intercept_ = m.est.intercept_ + 1.5  # deliberately miscalibrated
    raw_gap = abs(m.raw(test).mean() - test["y"].mean())
    m.calibrate(cal, cal["y"].to_numpy())
    assert abs(m.predict(test).mean() - test["y"].mean()) < raw_gap / 3


def test_artifact_round_trip_and_skops_type_check():
    ds = _synthetic_ds()
    for algo in ("logistic", "gbm"):
        m = Candidate(algo, ["x1", "x2"]).fit(ds, ds["y"].to_numpy()).calibrate(ds.tail(500), ds.tail(500)["y"].to_numpy())
        art = to_artifact(m)
        assert np.allclose(from_artifact(art).predict(ds.head(50)), m.predict(ds.head(50)), atol=1e-9)
        if algo == "logistic":
            assert "skops_b64" not in art and isinstance(art["coef"], list)  # plain JSON, no executable payload
    import base64

    import skops.io as sio

    class Evil:  # an unexpected type smuggled into a gbm artifact must be refused
        pass
    bad = {"algo": "gbm", "features": ["x1"], "median": {"x1": 0.0}, "isotonic": None,
           "skops_b64": base64.b64encode(sio.dumps({"obj": Evil()})).decode()}
    with pytest.raises(ValueError, match="unexpected types"):
        from_artifact(bad)


def test_baseline_uses_training_window_only():
    ds = _synthetic_ds()
    train, test = ds.iloc[:1000].copy(), ds.iloc[1000:1200].copy()
    test["y"] = 1  # future labels must not leak into the baseline
    b = baseline_predict(train, test)
    assert np.all(b < 0.6)


def test_gate_requires_enough_oos():
    small = {"model": {"n": 50, "brier": 0.1, "auc": 0.8}, "baseline": {"n": 50, "brier": 0.2, "auc": 0.5}}
    assert not gate(small, CFG)["eligible"]


# ------------------------------------------------------------------ regime model & ensemble score
def test_ml_regime_names_and_agreement():
    from datetime import date

    from app.providers.sample import BENCHMARK, get_universe
    from engine.ml.regime_ml import fit_predict

    b = get_universe(date(2026, 9, 29)).bars[BENCHMARK]
    r1, r2 = fit_predict(b), fit_predict(b)
    assert r1["available"] and r1["state"] == r2["state"] and abs(sum(r1["state_probabilities"].values()) - 1) < 0.005  # values rounded to 3 dp
    names = [s["name"] for s in r1["states"]]
    assert len(names) == len(set(names)) == 4 and len(r1["state_probabilities"]) == 4  # one-to-one, no merged states
    assert {n.split(" (")[0] for n in names} <= {"Calm uptrend", "Volatile uptrend", "Calm drift / range", "Volatile selloff", "Downtrend"}
    assert "Context only" in r1["note"]


def test_historical_component_is_visible_but_unweighted_by_default():
    from engine.scoring import combine, score_historical
    from engine.config import DEFAULT_WEIGHTS

    assert DEFAULT_WEIGHTS["historical"] == 0 and DEFAULT_WEIGHTS["ml"] == 0
    strong = score_historical({"sample_size": 300, "expectancy_r": 0.4, "t1_hit_rate": 55})
    weak = score_historical({"sample_size": 300, "expectancy_r": -0.3, "t1_hit_rate": 30})
    thin = score_historical({"sample_size": 35, "expectancy_r": 0.4, "t1_hit_rate": 55})
    assert strong > 80 > 50 > weak and 50 < thin < strong and score_historical({"sample_size": 10, "expectancy_r": 1}) is None
    comps = {"trend": 80.0, "historical": 20.0}
    assert combine(comps, {"trend": 1, "historical": 0}) == 80.0 and combine(comps, {"trend": 1, "historical": 1}) == 50.0


# ------------------------------------------------------------------ lifecycle API
def test_train_gate_activation_annotation_and_drift(app_client, admin_headers, scanned):
    r = app_client.post(f"{API}/admin/jobs/ml-train?market=NSE", headers=admin_headers)
    job = app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()
    assert job["status"] == "done", job
    mid = job["result"]["model_id"]
    m = app_client.get(f"{API}/ml/models/{mid}", headers=admin_headers).json()
    assert m["status"] == "candidate" and m["results"][m["algo"]]["oos"]["model"]["n"] > 0 and m["importance"]
    assert m["is_sample_data"] is True
    if not m["eligible"]:  # synthetic data: the gate should normally refuse
        assert app_client.patch(f"{API}/ml/models/{mid}", json={"status": "active"}, headers=admin_headers).status_code == 409
    # force eligibility directly in the DB to exercise serving + drift (tests only)
    from app.core.db import SessionLocal
    from app.models import MLModel, Signal, SignalOutcome

    db = SessionLocal()
    row = db.get(MLModel, mid)
    row.eligible = True
    db.commit()
    db.close()
    assert app_client.patch(f"{API}/ml/models/{mid}", json={"status": "active"}, headers=admin_headers).json()["status"] == "active"
    r = app_client.post(f"{API}/admin/jobs/scan?market=NSE", headers=admin_headers)
    assert app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()["status"] == "done"
    items = app_client.get(f"{API}/signals?status=NO_TRADE", headers=admin_headers).json()["items"]
    trained = [s for s in items if s.get("ml", {}).get("available")]
    for s in trained:
        assert 0 < s["ml"]["probability_t1_pct"] < 100 and s["ml"]["model_id"] == mid and "not instead of" in s["ml"]["note"]
        assert s["components"]["ml"] == s["ml"]["probability_t1_pct"]
    assert app_client.get(f"{API}/ml/regime?market=NSE", headers=admin_headers).json()["available"]
    # drift: fabricate 60 resolved outcomes where the model was confidently wrong and the empirical rate right
    db = SessionLocal()
    base = db.query(Signal).filter(Signal.market == "NSE").first()
    for k in range(60):
        payload = {**base.payload, "ml": {"model_id": mid, "probability_t1_pct": 95.0}, "probability": {"t1_hit_rate": 30.0}}
        s = Signal(scan_run_id=base.scan_run_id, instrument_id=base.instrument_id, symbol=f"DRIFT{k}", market="NSE", strategy_key="x", direction="LONG",
                   status="VALID", as_of=base.as_of, score=70, rr_t2=2.5, sample_size=50, payload=payload)
        db.add(s)
        db.flush()
        db.add(SignalOutcome(signal_id=s.id, status="resolved", t1_hit=False))
    db.commit()
    from app.services.ml_service import check_drift

    out = check_drift(db, "NSE")
    assert out["action"] == "suspended" and db.get(MLModel, mid).status == "suspended"
    db.query(SignalOutcome).filter(SignalOutcome.signal_id.in_(db.query(Signal.id).filter(Signal.symbol.like("DRIFT%")))).delete(synchronize_session=False)
    db.query(Signal).filter(Signal.symbol.like("DRIFT%")).delete(synchronize_session=False)
    db.commit()
    db.close()
    std_models = app_client.get(f"{API}/ml/models", headers=admin_headers).json()
    assert any(x["id"] == mid and x["status"] == "suspended" for x in std_models["items"])


def test_pattern_strategies_have_history(app_client, admin_headers, scanned):
    s = {x["id"]: x for x in app_client.get(f"{API}/strategies", headers=admin_headers).json()["builtin"]}
    assert {"geo_breakout", "geo_breakdown", "cup_handle"} <= set(s)
    assert s["cup_handle"]["performance"] is None or s["cup_handle"]["performance"]["trades"] >= 0



def test_gate_rejects_a_lucky_margin():
    """A model that is only marginally better by chance must not pass: the bootstrap check catches it."""
    rng = np.random.default_rng(0)
    n = 800
    y = (rng.random(n) < 0.3).astype(float)
    b = np.full(n, 0.3)
    p = b.copy()
    idx = rng.choice(n, 12, replace=False)
    p[idx] = np.where(y[idx] == 1, 0.45, 0.15)  # a handful of right calls…
    j = rng.choice(np.setdiff1d(np.arange(n), idx), 6, replace=False)
    p[j] = np.where(y[j] == 1, 0.15, 0.45)      # …and a few wrong: lower Brier, but by luck
    from engine.ml.model import metrics, paired_bootstrap

    oos = {"model": metrics(y, p), "baseline": metrics(y, b), "significance": paired_bootstrap(y, p, b, 1000)}
    assert oos["model"]["brier"] < oos["baseline"]["brier"] and oos["significance"]["p_model_better"] < 0.95
    g = gate(oos, TrainConfig(min_oos=100, brier_margin=1.0, min_auc=0.0))
    assert not g["eligible"] and any("statistically reliable" in r for r in g["reasons"])
