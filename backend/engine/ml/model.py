"""Training, purged walk-forward evaluation, isotonic calibration, activation gate, safe persistence.

Walk-forward fold k (expanding window, ordered by time):
    train:        events whose EXIT date < calib_start − embargo      (purged: no label overlaps later windows)
    calibration:  events signalled in [calib_start, test_start − embargo) and exited before test_start − embargo
    test:         events signalled in [test_start, test_end)
The reported metrics use only the concatenated out-of-sample test predictions.

Baseline = the platform's empirical estimate: T1 hit rate of the same strategy + regime family
computed from the training window only (falls back to strategy, then global). A model is
ELIGIBLE for activation only if it beats that baseline's Brier score out of sample.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

ALGOS = ("logistic", "gbm")


@dataclass
class TrainConfig:
    n_folds: int = 4
    test_fraction: float = 0.4     # last 40% of the timeline is split into test folds
    calib_fraction: float = 0.15   # of each fold's pre-test history, the most recent share calibrates
    embargo_days: int = 30         # ≥ max holding period, so no label straddles a boundary
    min_train: int = 300
    min_oos: int = 200
    brier_margin: float = 0.99     # model Brier must be ≤ 99% of baseline Brier
    min_auc: float = 0.52
    confidence: float = 0.95       # …and the improvement must hold in ≥95% of paired bootstrap resamples
    bootstrap: int = 2000

    def to_dict(self):
        return asdict(self)


# ------------------------------------------------------------------ models
def _impute(X: pd.DataFrame, med: Optional[pd.Series] = None) -> Tuple[np.ndarray, pd.Series]:
    med = X.median(numeric_only=True).fillna(0.0) if med is None else med
    return X.fillna(med).to_numpy(dtype=float), med


class Candidate:
    """Base estimator + median imputation + standardisation (logistic) + isotonic calibrator."""

    def __init__(self, algo: str, features: List[str]):
        self.algo, self.features = algo, features
        self.med = self.mu = self.sd = None
        self.est = None
        self.iso: Optional[IsotonicRegression] = None

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> "Candidate":
        A, self.med = _impute(X[self.features])
        if self.algo == "logistic":
            self.mu, self.sd = A.mean(axis=0), A.std(axis=0)
            self.sd[self.sd == 0] = 1.0
            self.est = LogisticRegression(C=0.3, max_iter=3000)
            self.est.fit((A - self.mu) / self.sd, y)
        else:
            self.est = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, l2_regularization=1.0,
                                                      min_samples_leaf=40, random_state=0)
            self.est.fit(A, y)
        return self

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        A, _ = _impute(X[self.features], self.med)
        if self.algo == "logistic":
            A = (A - self.mu) / self.sd
        return self.est.predict_proba(A)[:, 1]

    def calibrate(self, X: pd.DataFrame, y: np.ndarray) -> "Candidate":
        if len(y) >= 50 and 0 < y.mean() < 1:
            self.iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(self.raw(X), y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        p = self.raw(X)
        return self.iso.predict(p) if self.iso is not None else p


# ------------------------------------------------------------------ baseline (the empirical estimate)
def baseline_predict(train: pd.DataFrame, test: pd.DataFrame, min_n: int = 30) -> np.ndarray:
    glob = float(train["y"].mean())
    by_sr = train.groupby(["strategy_id", "regime_family"])["y"].agg(["mean", "count"])
    by_s = train.groupby("strategy_id")["y"].agg(["mean", "count"])
    out = []
    for s, r in zip(test["strategy_id"], test["regime_family"]):
        if (s, r) in by_sr.index and by_sr.loc[(s, r), "count"] >= min_n:
            out.append(by_sr.loc[(s, r), "mean"])
        elif s in by_s.index and by_s.loc[s, "count"] >= min_n:
            out.append(by_s.loc[s, "mean"])
        else:
            out.append(glob)
    return np.clip(np.array(out, dtype=float), 0.01, 0.99)


# ------------------------------------------------------------------ metrics
def paired_bootstrap(y: np.ndarray, p: np.ndarray, b: np.ndarray, n: int = 2000, seed: int = 0) -> Dict:
    """Resample out-of-sample trades with replacement; how often is the model's Brier lower than the baseline's?
    (Paired: model and baseline are scored on the same resampled trades.)"""
    d = (p - y) ** 2 - (b - y) ** 2  # per-trade squared-error difference (negative = model better)
    rng = np.random.default_rng(seed)
    means = np.array([d[rng.integers(0, len(d), len(d))].mean() for _ in range(n)])
    return {"mean_brier_diff": round(float(d.mean()), 6), "ci95": [round(float(np.percentile(means, 2.5)), 6), round(float(np.percentile(means, 97.5)), 6)],
            "p_model_better": round(float((means < 0).mean()), 4), "resamples": n}


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> List[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & (p < b if b < 1 else p <= b)
        if m.sum():
            out.append({"bin": [round(a, 2), round(b, 2)], "mean_predicted": round(float(p[m].mean()), 4),
                        "observed_rate": round(float(y[m].mean()), 4), "count": int(m.sum())})
    return out


def metrics(y: np.ndarray, p: np.ndarray) -> Dict:
    auc = float(roc_auc_score(y, p)) if 0 < y.mean() < 1 else None
    return {"n": int(len(y)), "base_rate": round(float(y.mean()), 4), "brier": round(float(brier_score_loss(y, p)), 5),
            "log_loss": round(float(log_loss(y, np.clip(p, 1e-4, 1 - 1e-4))), 5), "auc": None if auc is None else round(auc, 4),
            "mean_predicted": round(float(p.mean()), 4)}


# ------------------------------------------------------------------ walk-forward
def fold_windows(dates: pd.Series, cfg: TrainConfig) -> List[Dict[str, pd.Timestamp]]:
    start, end = dates.min(), dates.max()
    test_start0 = start + (end - start) * (1 - cfg.test_fraction)
    step = (end - test_start0) / cfg.n_folds
    out = []
    for k in range(cfg.n_folds):
        ts, te = test_start0 + step * k, test_start0 + step * (k + 1)
        if k == cfg.n_folds - 1:
            te = end + pd.Timedelta(days=1)  # last fold includes the final signal date
        cs = start + (ts - start) * (1 - cfg.calib_fraction)
        out.append({"calib_start": cs, "test_start": ts, "test_end": te})
    return out


def split(ds: pd.DataFrame, w: Dict[str, pd.Timestamp], embargo: pd.Timedelta):
    train = ds[ds["exit_date"] < w["calib_start"] - embargo]
    calib = ds[(ds["signal_date"] >= w["calib_start"]) & (ds["exit_date"] < w["test_start"] - embargo)]
    test = ds[(ds["signal_date"] >= w["test_start"]) & (ds["signal_date"] < w["test_end"])]
    return train, calib, test


def walk_forward(ds: pd.DataFrame, features: List[str], algo: str, cfg: TrainConfig) -> Dict:
    emb = pd.Timedelta(days=cfg.embargo_days)
    ys, ps, bs, folds = [], [], [], []
    for w in fold_windows(ds["signal_date"], cfg):
        train, calib, test = split(ds, w, emb)
        if len(train) < cfg.min_train or len(test) == 0:
            folds.append({"test": [str(w["test_start"].date()), str(w["test_end"].date())], "skipped": "insufficient training data"})
            continue
        m = Candidate(algo, features).fit(train, train["y"].to_numpy()).calibrate(calib, calib["y"].to_numpy())
        p = m.predict(test)
        b = baseline_predict(train, test)
        y = test["y"].to_numpy()
        ys.append(y), ps.append(p), bs.append(b)
        folds.append({"train_end": str((w["calib_start"] - emb).date()), "calib": [str(w["calib_start"].date()), str((w["test_start"] - emb).date())],
                      "test": [str(w["test_start"].date()), str(w["test_end"].date())], "n_train": int(len(train)), "n_calib": int(len(calib)),
                      "model": metrics(y, p), "baseline": metrics(y, b)})
    if not ys:
        return {"folds": folds, "oos": None}
    y, p, b = np.concatenate(ys), np.concatenate(ps), np.concatenate(bs)
    return {"folds": folds, "oos": {"model": metrics(y, p), "baseline": metrics(y, b), "reliability_model": reliability(y, p),
                                    "reliability_baseline": reliability(y, b), "significance": paired_bootstrap(y, p, b, cfg.bootstrap)}}


def gate(oos: Optional[Dict], cfg: TrainConfig) -> Dict:
    if not oos:
        return {"eligible": False, "reasons": ["No out-of-sample folds could be evaluated"]}
    m, b = oos["model"], oos["baseline"]
    reasons = []
    if m["n"] < cfg.min_oos:
        reasons.append(f"Only {m['n']} out-of-sample trades (min {cfg.min_oos})")
    if m["brier"] > b["brier"] * cfg.brier_margin:
        reasons.append(f"OOS Brier {m['brier']} does not beat the empirical baseline {b['brier']} by ≥{round(100 * (1 - cfg.brier_margin))}%")
    if m["auc"] is None or m["auc"] < cfg.min_auc:
        reasons.append(f"OOS AUC {m['auc']} below {cfg.min_auc}")
    sig = oos.get("significance")
    if sig is not None and sig["p_model_better"] < cfg.confidence:
        reasons.append(f"Improvement not statistically reliable: model beat the baseline in {100 * sig['p_model_better']:.1f}% of paired "
                       f"bootstrap resamples (need ≥ {100 * cfg.confidence:.0f}%)")
    return {"eligible": not reasons, "reasons": reasons or ["Beats the empirical baseline out of sample"]}


def train_final(ds: pd.DataFrame, features: List[str], algo: str, cfg: TrainConfig) -> Candidate:
    """Fit on all but the most recent calibration window; calibrate on that window."""
    emb = pd.Timedelta(days=cfg.embargo_days)
    start, end = ds["signal_date"].min(), ds["signal_date"].max()
    cs = start + (end - start) * (1 - cfg.calib_fraction)
    train = ds[ds["exit_date"] < cs - emb]
    calib = ds[ds["signal_date"] >= cs]
    return Candidate(algo, features).fit(train, train["y"].to_numpy()).calibrate(calib, calib["y"].to_numpy())


def importance(model: Candidate, test: pd.DataFrame, top: int = 12, seed: int = 0) -> List[dict]:
    """Permutation importance (increase in Brier when a feature is shuffled) on out-of-sample rows."""
    if len(test) < 50:
        return []
    rng = np.random.default_rng(seed)
    y = test["y"].to_numpy()
    base = brier_score_loss(y, model.predict(test))
    out = []
    for f in model.features:
        if test[f].nunique(dropna=True) <= 1:
            continue
        sh = test.copy()
        sh[f] = rng.permutation(sh[f].to_numpy())
        out.append({"feature": f, "brier_increase": round(float(brier_score_loss(y, model.predict(sh)) - base), 5)})
    return sorted(out, key=lambda r: -r["brier_increase"])[:top]


# ------------------------------------------------------------------ persistence (no pickle)
GBM_TRUSTED = ["sklearn.ensemble._hist_gradient_boosting.gradient_boosting.HistGradientBoostingClassifier",
               "sklearn.ensemble._hist_gradient_boosting.binning._BinMapper",
               "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
               "sklearn._loss.link.LogitLink", "sklearn._loss.link.Interval", "sklearn._loss.loss.HalfBinomialLoss",
               "sklearn._loss._loss.CyHalfBinomialLoss", "sklearn._loss.link.MultinomialLogit", "numpy.dtype", "builtins.slice"]


def to_artifact(m: Candidate) -> Dict:
    art = {"algo": m.algo, "features": m.features, "median": {k: float(v) for k, v in m.med.items()},
           "isotonic": None if m.iso is None else {"x": m.iso.X_thresholds_.tolist(), "y": m.iso.y_thresholds_.tolist()}}
    if m.algo == "logistic":
        art.update({"coef": m.est.coef_[0].tolist(), "intercept": float(m.est.intercept_[0]), "mu": m.mu.tolist(), "sd": m.sd.tolist()})
    else:
        import skops.io as sio

        art["skops_b64"] = base64.b64encode(sio.dumps(m.est)).decode()
    return art


def from_artifact(art: Dict) -> "LoadedModel":
    return LoadedModel(art)


class LoadedModel:
    """Inference-only model rebuilt from a JSON artifact (logistic) or a type-checked skops payload (gbm)."""

    def __init__(self, art: Dict):
        self.art = art
        self.features = art["features"]
        self.med = pd.Series(art["median"])
        self.est = None
        if art["algo"] == "gbm":
            import skops.io as sio

            payload = base64.b64decode(art["skops_b64"])
            untrusted = sio.get_untrusted_types(data=payload)
            bad = [t for t in untrusted if t not in GBM_TRUSTED]
            if bad:
                raise ValueError(f"Refusing to load model artifact with unexpected types: {bad}")
            self.est = sio.loads(payload, trusted=untrusted)

    def raw(self, X: pd.DataFrame) -> np.ndarray:
        A = X.reindex(columns=self.features).fillna(self.med).to_numpy(dtype=float)
        if self.art["algo"] == "logistic":
            z = ((A - np.array(self.art["mu"])) / np.array(self.art["sd"])) @ np.array(self.art["coef"]) + self.art["intercept"]
            return 1 / (1 + np.exp(-z))
        return self.est.predict_proba(A)[:, 1]

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        p = self.raw(X)
        iso = self.art.get("isotonic")
        return np.interp(p, iso["x"], iso["y"]) if iso else p


def train_and_select(ds: pd.DataFrame, features: List[str], cfg: Optional[TrainConfig] = None) -> Dict:
    """Evaluate every algorithm with walk-forward; pick the lowest OOS Brier; fit the final model."""
    cfg = cfg or TrainConfig()
    results = {a: walk_forward(ds, features, a, cfg) for a in ALGOS}
    scored = {a: r for a, r in results.items() if r["oos"]}
    if not scored:
        return {"selected": None, "results": results, "gate": gate(None, cfg), "config": cfg.to_dict()}
    best = min(scored, key=lambda a: scored[a]["oos"]["model"]["brier"])
    g = gate(scored[best]["oos"], cfg)
    final = train_final(ds, features, best, cfg)
    last = fold_windows(ds["signal_date"], cfg)[-1]
    _, _, test = split(ds, last, pd.Timedelta(days=cfg.embargo_days))
    imp_model = Candidate(best, features).fit(*_xy(ds[ds["exit_date"] < last["calib_start"] - pd.Timedelta(days=cfg.embargo_days)]))
    return {"selected": best, "results": results, "gate": g, "config": cfg.to_dict(), "artifact": to_artifact(final),
            "importance": importance(imp_model, test), "n_events": int(len(ds)), "period": [str(ds["signal_date"].min().date()), str(ds["exit_date"].max().date())]}


def _xy(df: pd.DataFrame):
    return df, df["y"].to_numpy()

