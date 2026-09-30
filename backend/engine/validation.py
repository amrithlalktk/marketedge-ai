"""Signal validation / NO TRADE engine.

Every candidate setup passes through the same checklist. Any BLOCK failure
turns the result into NO_TRADE; WARN failures are surfaced as risk factors.
The engine never relaxes a rule to fill the dashboard.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import List, Optional

import numpy as np
import pandas as pd

from .config import ValidationConfig


@dataclass
class Check:
    name: str
    passed: bool
    severity: str  # block | warn
    detail: str

    def to_dict(self):
        return asdict(self)


def expected_last_session(today: date, holidays: Optional[set] = None, calendar: str = "weekdays") -> date:
    """Most recent session whose daily bar should be complete. 24x7 markets (crypto) close their
    daily bar at 00:00 UTC, so the last complete bar is yesterday's."""
    if calendar == "24x7":
        return (pd.Timestamp(today) - pd.Timedelta(days=1)).date()
    d = pd.Timestamp(today)
    holidays = holidays or set()
    while d.weekday() >= 5 or d.date() in holidays:
        d -= pd.Timedelta(days=1)
    return d.date()


def validate(*, f: pd.DataFrame, direction: str, levels, score: float, prob: dict, regime: Optional[dict],
             mtf: Optional[dict], today: date, cfg: ValidationConfig, earnings_in_days: Optional[int] = None,
             data_meta: Optional[dict] = None, holidays: Optional[set] = None, is_index: bool = False,
             calendar: str = "weekdays", liquidity_mult: float = 1.0, liquidity_currency: str = "INR") -> List[Check]:
    """`is_index`: no meaningful traded volume (indices, forex); volume/liquidity checks move to
    the instruments actually traded or do not apply. `liquidity_mult` converts the instrument's
    traded value into `liquidity_currency` (the currency of cfg.min_avg_traded_value)."""
    row = f.iloc[-1]
    checks: List[Check] = []
    add = lambda n, ok, sev, d: checks.append(Check(n, bool(ok), sev, d))

    # 1. Data freshness
    last_bar = f.index[-1].date()
    expected = expected_last_session(today, holidays, calendar)
    lag = (pd.Timestamp(expected) - pd.Timestamp(last_bar)).days
    add("Data freshness", lag <= cfg.max_staleness_days, "block", f"Last bar {last_bar}; expected session {expected} (lag {lag} days)")

    # 12. Data quality
    recent = f.iloc[-60:]
    nan_ok = recent[["open", "high", "low", "close", "volume"]].isna().sum().sum() == 0
    zero_vol = 0 if is_index else int((recent["volume"] <= 0).sum())
    ohlc_ok = bool(((recent["high"] >= recent[["open", "close"]].max(axis=1)) & (recent["low"] <= recent[["open", "close"]].min(axis=1))).all())
    jump = float(recent["close"].pct_change().abs().max() or 0)
    add("Data quality", nan_ok and zero_vol <= 2 and ohlc_ok and jump < 0.35, "block",
        f"NaNs: {'none' if nan_ok else 'present'}, zero-volume bars (60d): {zero_vol}, OHLC consistent: {ohlc_ok}, max 1-day move {100 * jump:.1f}%")
    add("History length", len(f) >= cfg.min_history_bars, "block", f"{len(f)} bars (min {cfg.min_history_bars})")

    # 2/3. Liquidity & spread proxy
    atv = float(row.get("avg_traded_value20", np.nan)) * liquidity_mult
    if is_index:
        add("Liquidity", True, "block", "No consolidated volume (index / forex) — traded-value liquidity does not apply")
    else:
        add("Liquidity", np.isfinite(atv) and atv >= cfg.min_avg_traded_value and row["close"] >= cfg.min_price, "block",
            (f"20-day avg traded value {atv / 1e7:.2f} Cr (min {cfg.min_avg_traded_value / 1e7:.1f} Cr); price {row['close']:.2f}"
             if liquidity_currency == "INR" else
             f"20-day avg traded value {liquidity_currency} {atv / 1e6:,.1f}M (min {cfg.min_avg_traded_value / 1e6:,.1f}M); price {row['close']:.4g}"))
    spread_proxy = float(((recent["high"] - recent["low"]) / recent["close"]).median())
    add("Spread / range sanity", spread_proxy < 0.08, "warn", f"Median daily range {100 * spread_proxy:.1f}% (bid/ask not available from daily data)")
    vr = float(row.get("vol_ratio20", np.nan))
    suspicious = (not is_index) and np.isfinite(vr) and vr >= cfg.suspicious_volume_mult and abs(float(row.get("ret1", 0))) < 0.01
    add("Volume sanity", not suspicious, "block", f"Volume {vr:.1f}× average with {100 * float(row.get('ret1', 0)):.1f}% move")

    # 8. Risk/reward and stop width
    add("Risk/reward", levels.rr_t2 >= cfg.min_rr_t2, "block", f"R:R to T2 = 1:{levels.rr_t2:.2f} (min 1:{cfg.min_rr_t2:g})")
    stop_pct = 100 * levels.risk_per_unit / levels.reference_price
    add("Stop width", stop_pct <= cfg.max_stop_pct, "block", f"Stop {stop_pct:.1f}% from entry (max {cfg.max_stop_pct:g}%)")
    add("Target distance", levels.rr_t1 >= 1.0, "block", f"T1 is {levels.rr_t1:.2f}R away (min 1R)")

    # Volatility abnormality
    rk = float(row.get("atr_pct_rank", np.nan))
    add("Volatility regime", not (np.isfinite(rk) and rk > cfg.abnormal_atr_percentile), "block", f"ATR% percentile {100 * rk:.0f} (block above {100 * cfg.abnormal_atr_percentile:.0f})")

    # 7. Market regime vs direction
    if regime:
        bad = (direction == "LONG" and regime["regime"] in ("Panic/Selloff",)) or (direction == "SHORT" and regime["regime"] == "Strong Bull")
        warn = (direction == "LONG" and regime["regime"] == "Bear") or (direction == "SHORT" and regime["family"] == "bull")
        add("Market regime", not bad, "block", f"{regime['regime']} market, volatility {regime['volatility']}")
        if warn and not bad:
            add("Regime headwind", False, "warn", f"{direction} setup against a {regime['regime']} market")
    else:
        add("Market regime", False, "warn", "Benchmark regime unavailable")

    # 4/5/6. Trend / momentum / volume confirmation & timeframe conflicts
    if mtf:
        add("Multi-timeframe alignment", mtf["alignment"] != "CONFLICT", "block", f"Alignment {mtf['alignment']}: " + ", ".join(f"{k} {v}" for k, v in mtf["timeframes"].items()))

    # 9/10. Historical evidence
    n = prob.get("sample_size", 0)
    add("Historical sample size", n >= cfg.min_sample_size, "block", f"{n} comparable historical trades (min {cfg.min_sample_size}); conditioning: {prob.get('conditioning', 'n/a')}")
    exp_r = prob.get("expectancy_r")
    add("Backtest validity", exp_r is not None and exp_r > 0, "block", f"Historical expectancy {exp_r if exp_r is not None else 'n/a'} R per trade after costs")

    # 11. Event risk
    if earnings_in_days is not None:
        add("Event risk (earnings)", earnings_in_days > cfg.earnings_block_days, "block", f"Earnings in {earnings_in_days} days")
        if cfg.earnings_block_days < earnings_in_days <= cfg.earnings_warn_days:
            add("Earnings approaching", False, "warn", f"⚠ Earnings in {earnings_in_days} days — high event risk")
    else:
        add("Event calendar", False, "warn", "Earnings/event calendar not available for this instrument")

    add("Setup score", score >= cfg.min_score, "block", f"Score {score:.0f} (min {cfg.min_score:.0f})")
    if data_meta and data_meta.get("is_sample"):
        add("Sample data", False, "warn", "SAMPLE/SYNTHETIC data — not real market prices. For development only.")
    return checks


def verdict(checks: List[Check]) -> str:
    return "NO_TRADE" if any((not c.passed) and c.severity == "block" for c in checks) else "VALID"
