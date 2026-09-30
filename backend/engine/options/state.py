"""Underlying market-state classification for option decisions.

Combines the daily feature frame of the index, the market regime, implied
volatility percentile and (when available) intraday bars."""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from .. import indicators as ind
from ..mtf import trend_state


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def intraday_context(bars_5m: Optional[pd.DataFrame]) -> Optional[Dict]:
    if bars_5m is None or len(bars_5m) < 100:
        return None
    vwap = ind.session_vwap(bars_5m)
    last_day = bars_5m.index[-1].normalize()
    today = bars_5m[bars_5m.index.normalize() == last_day]
    close = float(bars_5m["close"].iloc[-1])
    tfs = {"5M": trend_state(bars_5m), "15M": trend_state(_resample(bars_5m, "15min")),
           "30M": trend_state(_resample(bars_5m, "30min")), "1H": trend_state(_resample(bars_5m, "60min"))}
    bull = sum(v == "Bullish" for v in tfs.values())
    bear = sum(v == "Bearish" for v in tfs.values())
    align = "STRONG BULLISH" if bull == 4 else "STRONG BEARISH" if bear == 4 else "MIXED" if bull and bear else ("LEANING BULLISH" if bull > bear else "LEANING BEARISH" if bear > bull else "NEUTRAL")
    return {
        "as_of": str(bars_5m.index[-1]), "session_vwap": round(float(vwap.iloc[-1]), 2), "price": round(close, 2),
        "price_vs_vwap": "above" if close > vwap.iloc[-1] else "below",
        "session_high": round(float(today["high"].max()), 2), "session_low": round(float(today["low"].min()), 2),
        "timeframes": tfs, "alignment": align,
        "note": "Intraday context is used for entry timing only; it is not part of the backtested daily rules.",
    }


def classify_state(f: pd.DataFrame, regime: Optional[dict], iv_pct: Optional[float], intraday: Optional[Dict]) -> Dict:
    row = f.iloc[-1]
    c = float(row["close"])
    reasons: List[str] = []
    bull_pts = bear_pts = 0
    for n in (20, 50):
        if c > row[f"ema{n}"]:
            bull_pts += 1
            reasons.append(f"Close above {n} EMA ({row[f'ema{n}']:.0f})")
        else:
            bear_pts += 1
            reasons.append(f"Close below {n} EMA ({row[f'ema{n}']:.0f})")
    if row["macd_hist"] > 0:
        bull_pts += 1
    else:
        bear_pts += 1
    if row["weekly_trend"] == 1:
        bull_pts += 1
    elif row["weekly_trend"] == -1:
        bear_pts += 1
    adx = float(row["adx"])
    if adx < 18 and abs(float(row["ret20"])) < 0.03:
        direction = "Range-bound"
        reasons.append(f"ADX {adx:.0f} and 20-day move {100 * row['ret20']:.1f}% — no trend")
    elif bull_pts >= 3:
        direction = "Bullish"
    elif bear_pts >= 3:
        direction = "Bearish"
    else:
        direction = "Range-bound"
        reasons.append("Trend signals conflict")
    events = []
    if bool(row.get("pat_breakout")):
        events.append("Breakout")
        reasons.append(f"Close above prior 20-session high ({row['hh20_prior']:.0f})")
    if bool(row.get("pat_breakdown")):
        events.append("Breakdown")
        reasons.append(f"Close below prior 20-session low ({row['ll20_prior']:.0f})")
    atr_rank = float(row["atr_pct_rank"]) if pd.notna(row["atr_pct_rank"]) else None
    if iv_pct is not None:
        vol = "High volatility" if iv_pct >= 70 else "Low volatility" if iv_pct <= 30 else "Normal volatility"
        reasons.append(f"ATM IV percentile {iv_pct:.0f}")
    elif atr_rank is not None:
        vol = "High volatility" if atr_rank >= 0.8 else "Low volatility" if atr_rank <= 0.2 else "Normal volatility"
        reasons.append(f"IV history insufficient — realised-volatility (ATR) percentile {100 * atr_rank:.0f} used instead")
    else:
        vol = "Unknown volatility"
    squeeze = pd.notna(row["bb_width_rank"]) and row["bb_width_rank"] <= 0.2
    if squeeze:
        reasons.append("Bollinger width in lowest 20% of 120 sessions (volatility contraction)")
    if intraday:
        reasons.append(f"Intraday: price {intraday['price_vs_vwap']} session VWAP {intraday['session_vwap']:.0f}; {intraday['alignment'].lower()}")
    return {
        "direction": direction, "events": events, "volatility": vol, "squeeze": bool(squeeze),
        "labels": [direction] + events + [vol], "adx": round(adx, 1), "rsi": round(float(row["rsi"]), 1),
        "regime": regime["regime"] if regime else None, "reasons": reasons, "as_of": str(f.index[-1].date()),
    }
