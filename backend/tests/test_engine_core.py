"""Engine correctness: causality (no look-ahead), trade simulation rules,
probability point-in-time filtering, NO TRADE validation, risk maths."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine import indicators as ind
from engine.backtest import simulate_trade
from engine.config import BacktestConfig, CostModel, EngineConfig, ValidationConfig
from engine.features import build_features
from engine.levels import compute_levels
from engine.metrics import hit_rates, wilson_interval
from engine.probability import estimate
from engine.regime import classify
from engine.risk import position_size, volatility_position_size
from engine.strategies import STRATEGIES, detect
from engine.structure import confirmed_pivots
from engine.validation import validate, verdict
from engine.walkforward import default_segments, walk_forward_windows


def random_walk(n=600, seed=1, drift=0.0004, vol=0.015, start="2020-01-01"):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, vol, n)
    close = 100 * np.exp(np.cumsum(r))
    open_ = np.concatenate([[100], close[:-1]]) * np.exp(rng.normal(0, vol / 4, n))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, vol / 2, n)))
    vol_ = rng.lognormal(13, 0.4, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol_}, index=pd.bdate_range(start, periods=n))


# ------------------------------------------------------------------ indicators
def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up).iloc[-1] == pytest.approx(100.0)
    df = random_walk()
    r = ind.rsi(df["close"]).dropna()
    assert r.between(0, 100).all()


def test_ema_matches_recursive_definition():
    s = pd.Series(np.linspace(1, 50, 50))
    e = ind.ema(s, 10)
    alpha = 2 / 11
    manual = s.iloc[0]
    for x in s.iloc[1:]:
        manual = alpha * x + (1 - alpha) * manual
    assert e.iloc[-1] == pytest.approx(manual)


def test_atr_constant_range():
    n = 50
    df = pd.DataFrame({"high": [11.0] * n, "low": [9.0] * n, "close": [10.0] * n})
    assert ind.atr(df["high"], df["low"], df["close"]).iloc[-1] == pytest.approx(2.0)


@pytest.mark.parametrize("cut", [300, 420, 555])
def test_features_have_no_look_ahead(cut):
    """Every feature at bar t computed on the full history must equal the value
    computed on history truncated at t. Otherwise the future leaked in."""
    df = random_walk(600)
    full = build_features(df)
    part = build_features(df.iloc[: cut + 1])
    ts = df.index[cut]
    cols = [c for c in part.columns if part[c].dtype != object]
    a, b = full.loc[ts, cols], part.loc[ts, cols]
    for c in cols:
        va, vb = a[c], b[c]
        if pd.isna(va) and pd.isna(vb):
            continue
        assert va == pytest.approx(vb, rel=1e-9, abs=1e-9), f"look-ahead in feature {c}"


def test_strategy_signals_have_no_look_ahead():
    df = random_walk(700, seed=3, drift=0.001)
    full = build_features(df)
    cut = 650
    part = build_features(df.iloc[: cut + 1])
    for sid, spec in STRATEGIES.items():
        assert bool(detect(spec, full).iloc[cut]) == bool(detect(spec, part).iloc[cut]), sid


def test_pivots_are_placed_on_confirmation_bar():
    highs = [1, 2, 3, 10, 3, 2, 1, 1, 1]
    df = pd.DataFrame({"high": highs, "low": [h - 0.5 for h in highs]}, index=pd.bdate_range("2021-01-01", periods=len(highs)))
    piv = confirmed_pivots(df, 3, 3)
    assert np.isnan(piv["pivot_high"].iloc[3])  # not known on the pivot bar itself
    assert piv["pivot_high"].iloc[6] == 10     # known 3 bars later


# ------------------------------------------------------------------ trade simulation
def bars(rows, atr=2.0):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.bdate_range("2022-01-03", periods=len(rows)))
    df["volume"] = 1e6
    df["atr"] = atr
    return df


BT = BacktestConfig(max_hold_bars=5, partial_at_t1=0.5, costs=CostModel(commission_pct=0.0, slippage_pct=0.0))


def test_target1_then_breakeven():
    f = bars([[100, 101, 99, 100], [100, 104, 99.5, 103], [103, 103.5, 99.8, 100], [100, 100.5, 99, 99.5]])
    tr = simulate_trade(f, 0, "LONG", stop=97, t1=103, t2=109, bt=BT, max_chase_atr=0.5)
    assert tr.t1_hit and not tr.t2_hit and not tr.stop_hit
    assert tr.exit_reason == "breakeven"
    assert tr.net_return_pct == pytest.approx(1.5)  # 50% at +3%, 50% at 0%


def test_same_bar_stop_and_target_assumes_stop():
    f = bars([[100, 101, 99, 100], [100, 110, 96, 100]])
    tr = simulate_trade(f, 0, "LONG", stop=97, t1=103, t2=109, bt=BT, max_chase_atr=0.5)
    assert tr.stop_hit and not tr.t1_hit


def test_gap_through_stop_fills_at_open():
    f = bars([[100, 101, 99, 100], [100, 101, 99, 100], [95, 96, 94, 95]])
    tr = simulate_trade(f, 0, "LONG", stop=97, t1=103, t2=109, bt=BT, max_chase_atr=0.5)
    assert tr.stop_hit and tr.exit_reason == "stop_gap" and tr.exit_price == 95


def test_chase_rule_skips_large_gap():
    f = bars([[100, 101, 99, 100], [102, 103, 101, 102]], atr=2.0)  # gap +2 > 0.5 ATR
    assert simulate_trade(f, 0, "LONG", 97, 103, 109, BT, 0.5) is None


def test_time_exit_and_unresolved_excluded():
    rows = [[100, 101, 99, 100]] + [[100, 100.5, 99.5, 100.2]] * 5
    f = bars(rows)
    tr = simulate_trade(f, 0, "LONG", 97, 103, 109, BT, 0.5)
    assert tr.neither and tr.exit_reason == "time"
    short = bars(rows[:3])
    assert simulate_trade(short, 0, "LONG", 97, 103, 109, BT, 0.5) is None  # data ended before resolution


def test_costs_reduce_return():
    f = bars([[100, 101, 99, 100], [100, 110, 99.5, 109]])
    costly = BacktestConfig(max_hold_bars=5, partial_at_t1=0.5, costs=CostModel(commission_pct=0.1, slippage_pct=0.05))
    a = simulate_trade(f, 0, "LONG", 97, 103, 109, BT, 0.5)
    b = simulate_trade(f, 0, "LONG", 97, 103, 109, costly, 0.5)
    assert b.net_return_pct < a.net_return_pct


def test_short_trade_mirror():
    f = bars([[100, 101, 99, 100], [100, 100.5, 96, 96.5], [96.5, 97, 90, 91]])
    tr = simulate_trade(f, 0, "SHORT", stop=103, t1=97, t2=91, bt=BT, max_chase_atr=0.5)
    assert tr.t1_hit and tr.t2_hit and tr.exit_reason == "target2" and tr.net_return_pct > 0


# ------------------------------------------------------------------ levels
def test_levels_are_ordered_and_rr_consistent():
    f = build_features(random_walk(500, seed=5))
    for direction in ("LONG", "SHORT"):
        lv = compute_levels(f, len(f) - 1, direction, EngineConfig().levels)
        s = 1 if direction == "LONG" else -1
        assert s * (lv.reference_price - lv.stop) > 0
        assert s * (lv.targets[0] - lv.reference_price) > 0
        assert s * (lv.targets[1] - lv.targets[0]) > 0 and s * (lv.targets[2] - lv.targets[1]) > 0
        assert lv.rr_t2 == pytest.approx(abs(lv.targets[1] - lv.reference_price) / lv.risk_per_unit, abs=0.02)
        assert 0.8 * lv.atr - 0.01 <= lv.risk_per_unit <= 3.5 * lv.atr + 0.01


# ------------------------------------------------------------------ probability
def _event(sym, sig, exit_, t1, stop, fam="bull", bucket="75+"):
    return {"symbol": sym, "strategy_id": "breakout_volume", "signal_date": sig, "exit_date": exit_, "t1_hit": t1, "t2_hit": False,
            "stop_hit": stop, "neither": not t1 and not stop, "net_return_pct": 2.0 if t1 else -2.0, "r_multiple": 1.0 if t1 else -1.0,
            "bars_held": 5, "regime_family": fam, "score_bucket": bucket}


def test_probability_is_point_in_time():
    ev = pd.DataFrame([_event("A", "2023-01-02", "2023-01-10", True, False)] * 40 + [_event("A", "2024-05-01", "2024-05-20", False, True)] * 40)
    p = estimate(ev, "breakout_volume", "2024-01-01", "bull", "75+", 30, "NSE", "1d")
    assert p["sample_size"] == 40 and p["t1_hit_rate"] == 100.0  # future losers excluded
    p2 = estimate(ev, "breakout_volume", "2025-01-01", "bull", "75+", 30, "NSE", "1d")
    assert p2["sample_size"] == 80 and p2["t1_hit_rate"] == 50.0


def test_probability_conditioning_falls_back_and_discloses():
    ev = pd.DataFrame([_event("A", "2023-01-02", "2023-01-10", True, False, "bear", "60-75")] * 35)
    p = estimate(ev, "breakout_volume", "2024-01-01", "bull", "75+", 30, "NSE", "1d")
    assert p["conditioning"] == "strategy (all regimes)" and p["sample_size"] == 35


def test_wilson_interval():
    lo, hi = wilson_interval(70, 100)
    assert lo < 70 < hi and 59 < lo < 62 and 77 < hi < 80


# ------------------------------------------------------------------ validation / NO TRADE
def _validate(today, prob, f=None):
    f = f if f is not None else build_features(random_walk(400, seed=9))
    lv = compute_levels(f, len(f) - 1, "LONG", EngineConfig().levels)
    cfg = ValidationConfig(min_avg_traded_value=0)
    return validate(f=f, direction="LONG", levels=lv, score=80, prob=prob, regime=None, mtf=None, today=today, cfg=cfg)


GOOD_PROB = {"sample_size": 100, "expectancy_r": 0.3, "conditioning": "strategy"}


def test_stale_data_blocks():
    f = build_features(random_walk(400, seed=9))
    fresh_day = f.index[-1].date()
    checks = _validate(date(fresh_day.year + 1, 1, 15), GOOD_PROB, f)
    assert verdict(checks) == "NO_TRADE"
    assert any(c.name == "Data freshness" and not c.passed for c in checks)


def test_insufficient_sample_blocks():
    f = build_features(random_walk(400, seed=9))
    checks = _validate(f.index[-1].date(), {"sample_size": 12, "expectancy_r": 0.5}, f)
    assert any(c.name == "Historical sample size" and not c.passed for c in checks)
    assert verdict(checks) == "NO_TRADE"


def test_negative_expectancy_blocks():
    f = build_features(random_walk(400, seed=9))
    checks = _validate(f.index[-1].date(), {"sample_size": 200, "expectancy_r": -0.1}, f)
    assert any(c.name == "Backtest validity" and not c.passed for c in checks)


# ------------------------------------------------------------------ regime, splits, risk, DSL
def test_regime_detects_bull_and_selloff():
    n = 400
    idx = pd.bdate_range("2020-01-01", periods=n)
    up = 100 * np.exp(np.cumsum(np.full(n, 0.002)))
    wiggle = 1 + 0.004 * np.sin(np.arange(n))
    df = pd.DataFrame({"close": up * wiggle, "high": up * wiggle * 1.01, "low": up * wiggle * 0.99}, index=idx)
    reg = classify(df)
    assert reg["family"].iloc[-1] == "bull"
    crash = df.copy()
    crash.iloc[-8:, :] = crash.iloc[-8:, :].to_numpy() * np.linspace(0.97, 0.80, 8)[:, None]
    assert classify(crash)["regime"].iloc[-1] == "Panic/Selloff"


def test_segments_and_walk_forward_windows_are_ordered_and_disjoint():
    seg = default_segments("2018-01-01", "2026-01-01")
    assert seg["training"][1] < seg["validation"][0] <= seg["validation"][1] < seg["out_of_sample"][0]
    for w in walk_forward_windows("2018-01-01", "2026-01-01", 3, 1):
        assert w["train"][1] < w["test"][0]


def test_position_size_spec_example():
    out = position_size(500000, 1, 1000, 950)
    assert out["max_risk"] == 5000 and out["risk_per_unit"] == 50 and out["quantity"] == 100 and out["max_loss"] == 5000


def test_position_size_capped_by_capital_and_lots():
    out = position_size(100000, 5, 1000, 999, lot_size=25)
    assert out["quantity"] % 25 == 0 and out["position_value"] <= 100000 and out["capped_by"] == "available capital"
    v = volatility_position_size(100000, 1, 100, atr_value=2, atr_mult=2)
    assert v["stop"] == 96 and v["quantity"] == 250




def test_hit_rates_counts():
    ev = [_event("A", "2023-01-02", "2023-01-10", True, False)] * 3 + [_event("A", "2023-02-02", "2023-02-10", False, True)]
    h = hit_rates(ev)
    assert h["sample_size"] == 4 and h["t1_hit_rate"] == 75.0 and h["stop_rate"] == 25.0
