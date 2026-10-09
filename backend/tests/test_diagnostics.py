"""Losing-trade diagnostics: same-bar ambiguity, point-in-time gate replay, loss profile, corporate-action audit."""
from __future__ import annotations

import pandas as pd
import pytest

from engine import diagnostics as dg
from engine.backtest import simulate_trade
from engine.config import BacktestConfig, CostModel
from tests.test_engine_core import bars

BT = BacktestConfig(max_hold_bars=5, partial_at_t1=0.5, costs=CostModel(commission_pct=0.0, slippage_pct=0.0))


def test_same_bar_stop_and_target_is_flagged_ambiguous_and_counted_as_a_stop():
    f = bars([[100, 101, 99, 100], [100, 110, 96, 100]])
    tr = simulate_trade(f, 0, "LONG", stop=97, t1=103, t2=109, bt=BT, max_chase_atr=0.5)
    assert tr.stop_hit and not tr.t1_hit and tr.ambiguous
    assert tr.risk_atr == pytest.approx(1.5)  # (100 − 97) / ATR 2


def test_clean_stop_is_not_ambiguous():
    f = bars([[100, 101, 99, 100], [100, 101, 96, 98]])
    tr = simulate_trade(f, 0, "LONG", stop=97, t1=103, t2=109, bt=BT, max_chase_atr=0.5)
    assert tr.stop_hit and not tr.ambiguous


def _ev(rows):
    cols = ["symbol", "strategy_id", "direction", "signal_date", "exit_date", "entry", "stop", "t1", "t2", "r_multiple", "t1_hit", "t2_hit",
            "stop_hit", "neither", "bars_held", "net_return_pct", "gross_return_pct", "mfe_r", "mae_r", "exit_reason", "regime",
            "regime_family", "score_at_signal", "score_bucket", "rr_t2_planned"]
    out = []
    for sig, ex, r in rows:
        win = r > 0
        out.append(["X", "s", "LONG", sig, ex, 100.0, 95.0, 106.0, 110.0, r, win, False, not win, False, 3, r * 5, r * 5 + 0.2,
                    max(r, 0.2), 0.3 if win else 1.0, "breakeven" if win else "stop", "Weak Bull", "bull", 70.0, "60-75", 2.0])
    return pd.DataFrame(out, columns=cols)


def test_gate_replay_uses_only_trades_that_exited_before_the_signal():
    # 3 losers that exited early, then a signal on 2022-03-01 while 5 big winners are still open (exit later)
    rows = [("2022-01-03", "2022-01-10", -1.0)] * 3 + [("2022-02-01", "2022-03-15", 3.0)] * 5 + [("2022-03-01", "2022-03-20", 1.0)]
    g = dg.gate_replay(_ev(rows), dg.GatePolicy("t", min_sample=3, levels=(2,)))
    probe = g[g["signal_ts"] == pd.Timestamp("2022-03-01")].iloc[0]
    assert probe["evidence_n"] == 3 and probe["evidence_mean_r"] == pytest.approx(-1.0)  # the open winners are invisible
    assert not probe["published"] and probe["gate_failed"] == "Backtest validity"


def test_lower_confidence_bound_is_stricter_than_a_plain_positive_mean():
    noisy = [("2021-01-%02d" % (i + 1), "2021-01-%02d" % (i + 2), r) for i, r in enumerate([2.0, -1.0, -1.0, 2.0, -1.0, -0.8])]
    rows = noisy + [("2021-02-01", "2021-02-05", 1.0)]
    ev = _ev(rows)
    plain = dg.gate_replay(ev, dg.GatePolicy("plain", min_sample=6, levels=(2,)))
    strict = dg.gate_replay(ev, dg.GatePolicy("lcb", min_sample=6, lcb_z=1.0, levels=(2,)))
    last = lambda g: g.iloc[-1]  # noqa: E731
    assert last(plain)["published"] and not last(strict)["published"]  # mean +0.03 R, but well inside the noise


def test_outcome_stats_and_drawdown():
    ev = dg._prepare(_ev([("2022-01-03", "2022-01-05", 2.0), ("2022-01-04", "2022-01-06", -1.0), ("2022-01-05", "2022-01-07", -1.0),
                          ("2022-01-06", "2022-01-08", 3.0)]))
    s = dg.outcome_stats(ev)
    assert s["trades"] == 4 and s["stop_rate"] == 50.0 and s["expectancy_r"] == pytest.approx(0.75)
    assert s["profit_factor"] == pytest.approx(2.5) and s["max_drawdown_r"] == pytest.approx(2.0)


def test_loss_profile_separates_fast_failures_and_trades_that_were_in_profit():
    ev = _ev([("2022-01-03", "2022-01-05", -1.0)] * 4)
    ev.loc[0, "bars_held"], ev.loc[1, "mfe_r"], ev.loc[2, "exit_reason"] = 1, 1.2, "stop_gap"
    p = dg.loss_profile(ev)
    assert p["stopped_trades"] == 4 and p["fast_failures_pct"] == 25.0
    assert p["were_in_profit_1r_pct"] == 25.0 and p["gap_through_stop_pct"] == 25.0


def test_corporate_action_audit_finds_a_split_but_not_a_crash():
    idx = pd.bdate_range("2023-01-02", periods=6)
    split = pd.DataFrame({"open": [100, 101, 50.5, 51, 52, 52], "close": [101, 101, 51, 51.5, 52, 52.5]}, index=idx)
    crash = pd.DataFrame({"open": [100, 100, 73, 74, 75, 74], "close": [100, 100, 72, 74, 74, 75]}, index=idx)  # −27%: not a split ratio
    g = dg.corporate_action_gaps({"SPLIT": split, "CRASH": crash})
    assert list(g["symbol"]) == ["SPLIT"] and g.iloc[0]["looks_like"] == "1:2"
    ev = pd.DataFrame({"symbol": ["SPLIT", "SPLIT", "CRASH"], "signal_date": ["2023-01-03", "2025-01-01", "2023-01-03"],
                       "exit_date": ["2023-01-06", "2025-01-10", "2023-01-06"]})
    assert list(dg.trades_spanning(ev, g)) == [True, False, False]


def test_fill_just_above_the_stop_is_skipped():
    # signal close 100, stop 99.2; next open 99.5 leaves 0.3 (< 0.5 × ATR 2) to the stop → no trade, not a +100 R outlier
    f = bars([[100, 101, 99, 100], [99.5, 112, 99.4, 110]])
    assert simulate_trade(f, 0, "LONG", stop=99.2, t1=103, t2=109, bt=BT, max_chase_atr=0.5, min_risk_atr=0.5) is None
    assert simulate_trade(f, 0, "LONG", stop=99.2, t1=103, t2=109, bt=BT, max_chase_atr=0.5).t2_hit  # old behaviour, for contrast


def test_acceptance_needs_out_of_sample_evidence_and_rejects_a_faded_edge():
    from engine.acceptance import UNVALIDATED, VALIDATED, evaluate
    from engine.config import ValidationConfig

    days = pd.bdate_range("2021-01-04", periods=600)
    good = [(str(d.date()), str((d + pd.Timedelta(days=3)).date()), 1.5 if i % 2 else -1.0) for i, d in enumerate(days)]
    faded = [(s, e, (1.5 if i % 2 else -1.0) if i < 360 else (0.8 if i % 2 else -1.0)) for i, (s, e, _) in enumerate(good)]
    cfg = ValidationConfig(accept_min_trades=100, accept_max_drawdown_r=1e9)
    ev = pd.concat([_ev(good).assign(strategy_id="good"), _ev(faded).assign(strategy_id="faded")], ignore_index=True)
    res = evaluate(ev, ["good", "faded", "missing"], "2023-06-01", cfg)
    assert res["good"]["status"] == VALIDATED and res["good"]["out_of_sample"]["trades"] >= 100
    assert res["faded"]["status"] == UNVALIDATED and any("out-of-sample expectancy" in r for r in res["faded"]["reasons"])
    assert res["missing"]["status"] == UNVALIDATED
    few = evaluate(ev[ev["strategy_id"] == "good"].head(80), ["good"], "2023-06-01", cfg)
    assert few["good"]["status"] == UNVALIDATED and "out-of-sample trades" in few["good"]["reasons"][0]
