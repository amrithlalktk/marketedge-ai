"""Option-chain analytics.

Input: a DataFrame with one row per contract for ONE snapshot:
    expiry (date), strike, option_type ('CE'|'PE'), bid, ask, ltp, volume, oi, oi_change
optionally iv (decimal). Spot/future and rates are passed separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .pricing import black76, forward, greeks, implied_vol, year_fraction


@dataclass
class ChainConfig:
    r: float = 0.065           # risk-free (approx. Indian T-bill yield)
    q: float = 0.012           # index dividend yield
    max_spread_pct: float = 3.0     # bid/ask spread as % of mid for "liquid"
    min_oi_lots: float = 500        # open interest in lots
    min_volume_lots: float = 200    # daily volume in lots
    min_premium: float = 5.0        # avoid lottery tickets


def enrich(chain: pd.DataFrame, spot: float, as_of: datetime, cfg: ChainConfig, lot_size: int) -> pd.DataFrame:
    """Adds T, forward, mid, spread %, implied vol (solved from mid when not supplied), Greeks, liquidity flag."""
    c = chain.copy()
    c["expiry"] = pd.to_datetime(c["expiry"]).dt.date
    c["T"] = [year_fraction(as_of, e) for e in c["expiry"]]
    c["dte"] = [(e - as_of.date()).days for e in c["expiry"]]
    c["forward"] = forward(spot, cfg.r, cfg.q, c["T"].to_numpy())
    c["is_call"] = c["option_type"] == "CE"
    c["mid"] = np.where((c["bid"] > 0) & (c["ask"] > 0), (c["bid"] + c["ask"]) / 2, c["ltp"])
    c["spread_pct"] = np.where(c["mid"] > 0, 100 * (c["ask"] - c["bid"]) / c["mid"], np.nan)
    solved = implied_vol(c["mid"].to_numpy(), c["forward"].to_numpy(), c["strike"].to_numpy(), c["T"].to_numpy(), cfg.r, c["is_call"].to_numpy())
    if "iv" in c.columns:
        c["iv"] = pd.to_numeric(c["iv"], errors="coerce")
        c["iv"] = c["iv"].where(c["iv"].notna() & (c["iv"] > 0), pd.Series(solved, index=c.index))
    else:
        c["iv"] = solved
    g = greeks(spot, c["strike"].to_numpy(), c["T"].to_numpy(), c["iv"].fillna(0).to_numpy(), cfg.r, cfg.q, c["is_call"].to_numpy())
    for k, v in g.items():
        c[k] = v
    c["oi_lots"] = c["oi"] / lot_size
    c["volume_lots"] = c["volume"] / lot_size
    c["liquid"] = ((c["spread_pct"] <= cfg.max_spread_pct) & (c["oi_lots"] >= cfg.min_oi_lots) & (c["volume_lots"] >= cfg.min_volume_lots)
                   & (c["mid"] >= cfg.min_premium) & c["iv"].notna())
    c["moneyness"] = np.log(c["strike"] / c["forward"])
    return c


def atm_strike(c: pd.DataFrame, forward_price: float) -> float:
    strikes = np.sort(c["strike"].unique())
    return float(strikes[np.argmin(np.abs(strikes - forward_price))])


def atm_iv(c: pd.DataFrame, expiry: date) -> Optional[float]:
    e = c[c["expiry"] == expiry]
    if e.empty:
        return None
    k = atm_strike(e, float(e["forward"].iloc[0]))
    iv = e[e["strike"] == k]["iv"].dropna()
    return float(iv.mean()) if len(iv) else None


def max_pain(c: pd.DataFrame, expiry: date) -> Optional[float]:
    """Strike at which total intrinsic value paid to option holders at expiry is minimal."""
    e = c[c["expiry"] == expiry]
    if e.empty:
        return None
    strikes = np.sort(e["strike"].unique())
    calls = e[e["is_call"]][["strike", "oi"]].to_numpy()
    puts = e[~e["is_call"]][["strike", "oi"]].to_numpy()
    pain = []
    for s in strikes:
        cv = np.sum(np.maximum(s - calls[:, 0], 0) * calls[:, 1]) if len(calls) else 0.0
        pv = np.sum(np.maximum(puts[:, 0] - s, 0) * puts[:, 1]) if len(puts) else 0.0
        pain.append(cv + pv)
    return float(strikes[int(np.argmin(pain))])


def pcr(c: pd.DataFrame, expiry: Optional[date] = None) -> Dict[str, Optional[float]]:
    e = c if expiry is None else c[c["expiry"] == expiry]
    call_oi, put_oi = e[e["is_call"]]["oi"].sum(), e[~e["is_call"]]["oi"].sum()
    call_v, put_v = e[e["is_call"]]["volume"].sum(), e[~e["is_call"]]["volume"].sum()
    return {"pcr_oi": round(float(put_oi / call_oi), 3) if call_oi else None,
            "pcr_volume": round(float(put_v / call_v), 3) if call_v else None,
            "pcr_oi_change": round(float(e[~e["is_call"]]["oi_change"].sum() / e[e["is_call"]]["oi_change"].sum()), 3)
            if e[e["is_call"]]["oi_change"].sum() > 0 else None}


def oi_levels(c: pd.DataFrame, expiry: date, spot: float, top: int = 3) -> Dict[str, List[dict]]:
    """Highest put OI below spot ~ support; highest call OI above spot ~ resistance (writers' positioning)."""
    e = c[c["expiry"] == expiry]
    puts = e[(~e["is_call"]) & (e["strike"] <= spot)].nlargest(top, "oi")
    calls = e[(e["is_call"]) & (e["strike"] >= spot)].nlargest(top, "oi")
    fmt = lambda df: [{"strike": float(r.strike), "oi": float(r.oi), "oi_change": float(r.oi_change)} for r in df.itertuples()]
    return {"support": fmt(puts), "resistance": fmt(calls)}


def oi_buildup(c: pd.DataFrame, expiry: date, prev_ltp: Optional[pd.Series] = None) -> List[dict]:
    """Classic price/OI interpretation per strike band around ATM:
    price↑ OI↑ long buildup · price↓ OI↑ short buildup · price↑ OI↓ short covering · price↓ OI↓ long unwinding.
    Requires the previous snapshot's LTP indexed by (expiry, strike, option_type) — strikes repeat across
    expiries, so the expiry is part of the key. Returns [] without a previous snapshot."""
    if prev_ltp is None:
        return []
    e = c[c["expiry"] == expiry].copy()
    prev = prev_ltp[~prev_ltp.index.duplicated(keep="last")].to_dict()
    e["prev"] = [prev.get((expiry, float(k), t)) for k, t in zip(e["strike"], e["option_type"])]
    e = e.dropna(subset=["prev"])
    out = []
    for r in e.itertuples():
        dp, doi = r.ltp - r.prev, r.oi_change
        if doi == 0 or dp == 0:
            continue
        label = ("Long buildup" if dp > 0 and doi > 0 else "Short buildup" if dp < 0 and doi > 0
                 else "Short covering" if dp > 0 else "Long unwinding")
        out.append({"strike": float(r.strike), "option_type": r.option_type, "price_change": round(float(dp), 2), "oi_change": float(doi), "interpretation": label})
    return out


def skew_25d(c: pd.DataFrame, expiry: date) -> Optional[float]:
    """25-delta put IV minus 25-delta call IV (vol points). Positive = downside protection bid."""
    e = c[(c["expiry"] == expiry) & c["iv"].notna()]
    calls, puts = e[e["is_call"]], e[~e["is_call"]]
    if calls.empty or puts.empty:
        return None
    ci = calls.iloc[(calls["delta"] - 0.25).abs().argmin()]["iv"]
    pi = puts.iloc[(puts["delta"] + 0.25).abs().argmin()]["iv"]
    return round(100 * float(pi - ci), 2)


def expected_move(spot: float, iv: float, days: float) -> float:
    """1-sigma move over `days` calendar days implied by IV."""
    return float(spot * iv * np.sqrt(days / 365))


def straddle_price(c: pd.DataFrame, expiry: date) -> Optional[float]:
    e = c[c["expiry"] == expiry]
    if e.empty:
        return None
    k = atm_strike(e, float(e["forward"].iloc[0]))
    legs = e[e["strike"] == k]["mid"]
    return float(legs.sum()) if len(legs) == 2 else None


def iv_percentile(history: pd.Series, current: float, min_obs: int = 60) -> Optional[float]:
    """Share of the trailing year's ATM IV observations below the current value (0–100)."""
    h = history.dropna().iloc[-252:]
    if len(h) < min_obs or current is None:
        return None
    return round(100 * float((h < current).mean()), 1)


def reprice(row: pd.Series, spot: float, days_forward: float, cfg: ChainConfig, iv: Optional[float] = None) -> float:
    """Model premium if the underlying moves to `spot` after `days_forward` calendar days, IV unchanged."""
    T = max(row["T"] - days_forward / 365, 0.0)
    F = forward(spot, cfg.r, cfg.q, T)
    return float(black76(F, row["strike"], T, iv if iv is not None else row["iv"], cfg.r, bool(row["is_call"])))
