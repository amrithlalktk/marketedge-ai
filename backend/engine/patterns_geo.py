"""Geometric chart patterns from CONFIRMED pivots (no look-ahead).

At every bar t only pivots whose confirmation bar is <= t are used, so the
pattern state at t is exactly what a trader could have drawn at t. Detected:

* Rectangle            – flat upper and lower boundaries (≥2 touches each)
* Ascending triangle   – flat top, rising lows
* Descending triangle  – falling highs, flat bottom
* Symmetrical triangle – falling highs, rising lows (converging)
* Cup and handle       – rounded U (not a V) between two similar rims, 12–35 % deep,
                         30–150 bars long, followed by a shallow handle (≤ ⅓ of the depth)

Breakout flags fire on the bar that closes beyond the boundary (by 0.1 ATR)
after the previous close was inside it; boundaries are the lines fitted to
pivots known at the previous bar.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

FLAT = 0.02      # |slope| in ATR per bar considered flat
SLOPED = 0.03    # slope in ATR per bar considered rising/falling
LOOKBACK = 80
MIN_SPAN = 15


_FIT_CACHE: Dict[tuple, Optional[Tuple[float, float]]] = {}


def _fit(points: List[Tuple[int, float]]):
    """Closed-form least-squares line; cached per pivot set (the fit does not depend on t)."""
    key = tuple(points)
    if key in _FIT_CACHE:
        return _FIT_CACHE[key]
    n = len(points)
    xs = [p[0] for p in points]
    if n < 2 or max(xs) == min(xs):
        _FIT_CACHE[key] = None
        return None
    mx = sum(xs) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (p[1] - my) for x, p in zip(xs, points)) / sxx
    res = (float(slope), float(my - slope * mx))
    if len(_FIT_CACHE) > 200_000:
        _FIT_CACHE.clear()
    _FIT_CACHE[key] = res
    return res


def _classify(highs, lows, t: int, atr: float):
    """Return (kind, upper_at_t, lower_at_t, upper_at_t_minus_1, lower_at_t_minus_1) or None."""
    hs = [p for p in highs if p[0] >= t - LOOKBACK][-3:]
    ls = [p for p in lows if p[0] >= t - LOOKBACK][-3:]
    if len(hs) < 2 or len(ls) < 2 or not np.isfinite(atr) or atr <= 0:
        return None
    first, last = min(hs[0][0], ls[0][0]), max(hs[-1][0], ls[-1][0])
    if last - first < MIN_SPAN:
        return None
    fh, fl = _fit(hs), _fit(ls)
    if fh is None or fl is None:
        return None
    sh, sl = fh[0] / atr, fl[0] / atr
    up = lambda x: fh[0] * x + fh[1]
    lo = lambda x: fl[0] * x + fl[1]
    height = (up(t) - lo(t)) / atr
    if height <= 0.5 or height > 12:
        return None
    # touches must actually sit on their line (within 0.5 ATR)
    if any(abs(p[1] - up(p[0])) > 0.5 * atr for p in hs) or any(abs(p[1] - lo(p[0])) > 0.5 * atr for p in ls):
        return None
    if abs(sh) < FLAT and abs(sl) < FLAT and 1.5 <= height <= 10:
        kind = "rectangle"
    elif abs(sh) < FLAT and sl > SLOPED:
        kind = "triangle_asc"
    elif sh < -SLOPED and abs(sl) < FLAT:
        kind = "triangle_desc"
    elif sh < -FLAT and sl > FLAT:
        kind = "triangle_sym"
    else:
        return None
    return kind, up(t), lo(t), up(t - 1), lo(t - 1)


def _cup_geometry(a: int, pa: float, b: int, pb: float, closes, lows, cache: dict):
    """Cup shape between two rims — independent of t, so computed once per rim pair."""
    key = (a, b)
    if key in cache:
        return cache[key]
    res = None
    length = b - a
    rim = max(pa, pb)
    if 30 <= length <= 150 and abs(pa - pb) / rim <= 0.05:
        seg = lows[a : b + 1]
        bottom_rel = int(np.argmin(seg))
        bottom = float(seg[bottom_rel])
        depth = (rim - bottom) / rim
        if 0.12 <= depth <= 0.35 and 0.25 <= bottom_rel / length <= 0.75:
            mid = closes[a + length // 4 : b - length // 4 + 1]  # rounded bottom: middle half stays in the lower part
            if len(mid) and float(np.mean(mid <= bottom + 0.4 * (rim - bottom))) >= 0.4:
                res = (rim, bottom, depth)
    cache[key] = res
    return res


def _cup(highs, closes, lows, t: int, atr: float, cache: Optional[dict] = None):
    """Cup-and-handle state at t using confirmed pivot highs as rims."""
    cache = {} if cache is None else cache
    recent = [p for p in highs if p[0] >= t - 175]
    best = None
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            (a, pa), (b, pb) = recent[i], recent[j]
            if not 3 <= t - b <= 25:
                continue
            geo = _cup_geometry(a, pa, b, pb, closes, lows, cache)
            if geo is None:
                continue
            rim, bottom, depth = geo
            handle_low = float(np.min(lows[b + 1 : t + 1])) if t > b else rim
            if handle_low < rim - (rim - bottom) / 3:  # handle deeper than a third of the cup
                continue
            cand = (rim, a, b, depth)
            if best is None or b > best[2]:
                best = cand
    return best


def detect(f: pd.DataFrame) -> pd.DataFrame:
    """f needs high, low, close, atr and the confirmed-pivot columns from structure.add_structure
    (pivot_high/pivot_low placed at the confirmation bar) plus pivot bar indices via confirmed_pivots."""
    from .structure import confirmed_pivots

    n = len(f)
    piv = confirmed_pivots(f)
    ph, pl = piv["pivot_high"].to_numpy(), piv["pivot_low"].to_numpy()
    phb, plb = piv["pivot_high_bar"].to_numpy(), piv["pivot_low_bar"].to_numpy()
    h, l, c, a = (f[k].to_numpy(dtype=float) for k in ("high", "low", "close", "atr"))
    cols: Dict[str, np.ndarray] = {k: np.zeros(n, dtype=bool) for k in (
        "pat_rectangle", "pat_triangle_asc", "pat_triangle_desc", "pat_triangle_sym", "pat_geo_breakout_up", "pat_geo_breakout_down",
        "pat_cup_handle", "pat_cup_handle_breakout")}
    upper, lower, rim_col = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []
    prev_state = None
    prev_cup = None
    cup_cache: dict = {}
    for t in range(n):
        if not np.isnan(ph[t]):
            highs.append((int(phb[t]), float(ph[t])))
        if not np.isnan(pl[t]):
            lows.append((int(plb[t]), float(pl[t])))
        state = _classify(highs, lows, t, a[t])
        # breakout vs the boundary known at t-1 (pattern must have existed before the breakout bar)
        if prev_state is not None and t > 0 and np.isfinite(a[t]):
            kind, up_prev, lo_prev, _, _ = prev_state
            up_t, lo_t = up_prev + (up_prev - prev_state[3]), lo_prev + (lo_prev - prev_state[4])  # extend lines one bar
            if c[t] > up_t + 0.1 * a[t] and c[t - 1] <= up_prev:
                cols["pat_geo_breakout_up"][t] = True
            if c[t] < lo_t - 0.1 * a[t] and c[t - 1] >= lo_prev:
                cols["pat_geo_breakout_down"][t] = True
        if state is not None:
            cols["pat_" + state[0]][t] = True
            upper[t], lower[t] = state[1], state[2]
        prev_state = state
        cup = _cup(highs, c, l, t, a[t], cup_cache) if np.isfinite(a[t]) else None
        if cup is not None:
            cols["pat_cup_handle"][t] = True
            rim_col[t] = cup[0]
        if prev_cup is not None and c[t] > prev_cup[0] and c[t - 1] <= prev_cup[0]:
            cols["pat_cup_handle_breakout"][t] = True
        prev_cup = cup
    out = pd.DataFrame(cols, index=f.index)
    out["geo_upper"], out["geo_lower"], out["cup_rim"] = upper, lower, rim_col
    return out


GEO_LABELS = {
    "pat_rectangle": "Rectangle (range) pattern",
    "pat_triangle_asc": "Ascending triangle",
    "pat_triangle_desc": "Descending triangle",
    "pat_triangle_sym": "Symmetrical triangle",
    "pat_geo_breakout_up": "Triangle/rectangle breakout (up)",
    "pat_geo_breakout_down": "Triangle/rectangle breakdown",
    "pat_cup_handle": "Cup and handle (forming)",
    "pat_cup_handle_breakout": "Cup-and-handle breakout",
}
