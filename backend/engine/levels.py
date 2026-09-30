"""Entry / stop / target engine. Adaptive to ATR and market structure.

`compute_levels` is called with the bar index of the signal and uses only
rows <= t, so the identical function drives live setups and backtests.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from .config import LevelConfig
from .structure import nearest_levels, sr_levels


@dataclass
class TradeLevels:
    direction: str
    reference_price: float  # signal-bar close
    entry_low: float
    entry_high: float
    stop: float
    targets: List[float]
    target_methods: List[str]
    stop_method: str
    risk_per_unit: float
    atr: float
    rr_t1: float
    rr_t2: float
    supports: List[float]
    resistances: List[float]

    def to_dict(self) -> dict:
        return asdict(self)


def price_decimals(ref: float) -> int:
    """Decimals appropriate to a price level: 2 for ≥ 1,000; 5 for EUR/USD ~1.1; 9 for sub-cent tokens."""
    if not ref or not np.isfinite(ref) or ref <= 0:
        return 2
    return int(min(10, max(2, 5 - np.floor(np.log10(ref)))))


def price_round(x: float, ref: float) -> float:
    return float(round(x, price_decimals(ref)))


def compute_levels(f: pd.DataFrame, t: int, direction: str, cfg: LevelConfig, entry_price: Optional[float] = None) -> Optional[TradeLevels]:
    row = f.iloc[t]
    a = float(row["atr"])
    if not np.isfinite(a) or a <= 0:
        return None
    ref = float(row["close"])
    entry = float(entry_price) if entry_price is not None else ref
    _r = lambda x: price_round(x, ref)  # noqa: E731
    sign = 1 if direction == "LONG" else -1

    # --- stop: structure first, ATR fallback -------------------------------------------
    look = f.iloc[max(0, t - cfg.swing_lookback + 1) : t + 1]
    if direction == "LONG":
        swing = look["pivot_low"].dropna()
        swing = swing[swing < entry]
        struct_stop = float(swing.iloc[-1]) - cfg.swing_buffer_atr * a if len(swing) else None
    else:
        swing = look["pivot_high"].dropna()
        swing = swing[swing > entry]
        struct_stop = float(swing.iloc[-1]) + cfg.swing_buffer_atr * a if len(swing) else None

    stop, stop_method = None, ""
    if struct_stop is not None:
        dist = abs(entry - struct_stop) / a
        if cfg.min_stop_atr <= dist <= cfg.max_stop_atr:
            stop = struct_stop
            stop_method = f"Below last confirmed swing {'low' if sign == 1 else 'high'} with 0.2×ATR buffer ({dist:.1f} ATR)"
    if stop is None:
        stop = entry - sign * cfg.atr_stop_mult * a
        stop_method = f"{cfg.atr_stop_mult:g}×ATR from entry (no swing level within 0.8–3.5 ATR)"
    risk = abs(entry - stop)

    # --- targets: S/R aware, R-multiple bounded ----------------------------------------
    levels = sr_levels(f, t, lookback=cfg.resistance_lookback)
    supports, resistances = nearest_levels(levels, entry)
    ahead = [lv.price for lv in (resistances if sign == 1 else supports)]
    t1, t1m = entry + sign * cfg.t1_r * risk, f"{cfg.t1_r:g}R (no qualifying S/R level)"
    for p in ahead:
        r_mult = abs(p - entry) / risk
        if 1.0 <= r_mult <= 2.0:
            t1, t1m = p, f"{'Resistance' if sign == 1 else 'Support'} level ({r_mult:.1f}R)"
            break
    t2, t2m = entry + sign * cfg.t2_r * risk, f"{cfg.t2_r:g}R (no qualifying S/R level beyond T1)"
    for p in ahead:
        if sign * (p - t1) >= 0.5 * risk and abs(p - entry) / risk <= cfg.t2_r + 1:
            t2, t2m = p, f"Next {'resistance' if sign == 1 else 'support'} level ({abs(p - entry) / risk:.1f}R)"
            break
    t3 = entry + sign * max(cfg.t3_r * risk, abs(t2 - entry) + risk)
    t3m = f"{cfg.t3_r:g}R extension"
    if sign == -1:  # a short target can never be at or below zero: cap at 95% of the entry price
        floor = entry * 0.05
        t1m = t1m + (" — capped at price floor" if t1 < floor else "")
        t2m = t2m + (" — capped at price floor" if t2 < floor else "")
        t3m = t3m + (" — capped at price floor" if t3 < floor else "")
        t1, t2, t3 = max(t1, floor), max(t2, floor * 0.99), max(t3, floor * 0.98)

    return TradeLevels(
        direction=direction,
        reference_price=_r(ref),
        entry_low=_r(min(ref, ref + sign * cfg.max_chase_atr * a)),
        entry_high=_r(max(ref, ref + sign * cfg.max_chase_atr * a)),
        stop=_r(stop),
        targets=[_r(t1), _r(t2), _r(t3)],
        target_methods=[t1m, t2m, t3m],
        stop_method=stop_method,
        risk_per_unit=_r(risk),
        atr=_r(a),
        rr_t1=round(abs(t1 - entry) / risk, 2),
        rr_t2=round(abs(t2 - entry) / risk, 2),
        supports=[_r(x.price) for x in supports[:3]],
        resistances=[_r(x.price) for x in resistances[:3]],
    )
