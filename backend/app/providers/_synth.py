"""Prefix-stable random streams for the SAMPLE generators.

Each stream is drawn once at a fixed maximum length from a generator seeded by a name,
then sliced. Generating one more day therefore APPENDS a bar without changing any earlier
bar — so incremental ingestion, paper orders and alerts behave as they would on real data.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache

import numpy as np

N_MAX = 40 * 366  # 40 years of daily steps is far beyond any sample history


def seed_of(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)


@lru_cache(maxsize=4096)
def _stream(key: str, dist: str, a: float, b: float) -> np.ndarray:
    rng = np.random.default_rng(seed_of(key + "|" + dist))
    if dist == "normal":
        return rng.normal(a, b, N_MAX)
    if dist == "uniform":
        return rng.uniform(a, b, N_MAX)
    if dist == "random":
        return rng.random(N_MAX)
    if dist == "lognormal":
        return rng.lognormal(a, b, N_MAX)
    raise ValueError(dist)


def draw(key: str, dist: str, n: int, a: float = 0.0, b: float = 1.0) -> np.ndarray:
    if n > N_MAX:
        raise ValueError("sample history too long")
    return _stream(key, dist, float(a), float(b))[:n].copy()


def scalar(key: str, a: float, b: float) -> float:
    """A fixed per-instrument parameter (does not depend on history length)."""
    return float(np.random.default_rng(seed_of(key + "|param")).uniform(a, b))


def trend_episodes(key: str, n: int, strength: float) -> np.ndarray:
    """Multi-week drift episodes; the draws consumed up to bar i never depend on n (prefix-stable)."""
    rng = np.random.default_rng(seed_of(key + "|trend"))
    t = np.zeros(n)
    i = 0
    while i < n:
        if rng.random() < 0.01:
            length = int(rng.integers(15, 60))
            t[i : i + length] = rng.normal(strength, strength * 0.6) * (1 if rng.random() < 0.6 else -1)
            i += length
        else:
            i += 1
    return t


def regime_market(key: str, n: int, scale: float = 1.0):
    """Regime-switching market factor (bull / range / bear / crash), prefix-stable."""
    reg = {"bull": (0.0007, 0.0085), "range": (0.0, 0.008), "bear": (-0.0007, 0.014), "crash": (-0.004, 0.028)}
    trans = {"bull": [0.985, 0.01, 0.004, 0.001], "range": [0.012, 0.975, 0.012, 0.001],
             "bear": [0.006, 0.012, 0.978, 0.004], "crash": [0.05, 0.05, 0.1, 0.8]}
    names = list(reg)
    u, z = draw(key + "-u", "random", n), draw(key + "-z", "normal", n)
    st, out, states = "bull", np.empty(n), []
    for i in range(n):
        mu, vol = reg[st]
        out[i] = scale * (mu + vol * z[i])
        states.append(st)
        cum = np.cumsum(trans[st])
        st = names[min(int(np.searchsorted(cum, u[i] * cum[-1])), 3)]
    return out, states


def ohlc(key: str, dates, r: np.ndarray, base: float, vol: float, volume_base: float, decimals: int = 2):
    import pandas as pd

    n = len(r)
    close = base * np.exp(np.cumsum(r))
    prev = np.concatenate([[base], close[:-1]])
    open_ = prev * np.exp(draw(key + "-gap", "normal", n, 0, 0.25 * vol))
    hi = np.maximum(open_, close) * np.exp(np.abs(draw(key + "-hi", "normal", n, 0, 0.5 * vol)))
    lo = np.minimum(open_, close) * np.exp(-np.abs(draw(key + "-lo", "normal", n, 0, 0.5 * vol)))
    if volume_base > 0:
        volume = volume_base * np.exp(draw(key + "-vol", "normal", n, 0, 0.3)) * (1 + 0.8 * np.abs(r) / max(vol, 1e-6))
    else:
        volume = np.zeros(n)
    df = pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close, "volume": np.round(volume)}, index=dates)
    return df.round({"open": decimals, "high": decimals, "low": decimals, "close": decimals})
