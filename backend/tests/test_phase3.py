"""Phase 3: rule DSL v2, portfolio simulator, hit-rate explorer, strategy versioning, scan inclusion, compare/CSV."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.features import build_features
from engine.portfolio import PortfolioConfig, simulate
from engine.probability import explore
from engine.strategies import StrategyDefinitionError, build_custom_strategy, detect
from tests.conftest import make_user
from tests.test_engine_core import random_walk

API = "/api/v1"


# ------------------------------------------------------------------ DSL v2
@pytest.fixture(scope="module")
def feats():
    return build_features(random_walk(500, seed=11, drift=0.0008))


def test_mult_and_shift_operands(feats):
    spec = build_custom_strategy({"name": "v", "direction": "LONG", "conditions": [
        {"left": "volume", "op": ">", "right": {"feature": "vol_sma20", "mult": 1.5}},
        {"left": "close", "op": ">", "right": {"feature": "close", "shift": 1}}]})
    sig = detect(spec, feats)
    expected = (feats["volume"] > 1.5 * feats["vol_sma20"]) & (feats["close"] > feats["close"].shift(1))
    assert (sig == expected.fillna(False)).all()
    assert spec.conditions == ["volume > 1.5×vol_sma20", "close > close[1]"]


def test_any_of_groups_are_or_of_ands(feats):
    spec = build_custom_strategy({"name": "g", "direction": "LONG", "conditions": [{"left": "close", "op": ">", "right": "ema50"}],
                                  "any_of": [[{"left": "rsi", "op": ">", "right": 70}], [{"left": "pat_breakout", "op": ">=", "right": 1}]]})
    sig = detect(spec, feats)
    exp = (feats["close"] > feats["ema50"]) & ((feats["rsi"] > 70) | feats["pat_breakout"].astype(bool))
    assert (sig == exp.fillna(False)).all()


def test_shift_cannot_look_forward_and_limits_enforced():
    base = {"name": "x", "direction": "LONG"}
    for bad in ({"feature": "close", "shift": -1}, {"feature": "close", "mult": 0}, {"feature": "__class__"}, {"feature": "close", "eval": 1}):
        with pytest.raises(StrategyDefinitionError):
            build_custom_strategy({**base, "conditions": [{"left": "close", "op": ">", "right": bad}]})
    with pytest.raises(StrategyDefinitionError):
        build_custom_strategy({**base, "conditions": [{"left": 1, "op": ">", "right": 2}]})
    with pytest.raises(StrategyDefinitionError):
        build_custom_strategy({**base, "conditions": [{"left": "rsi", "op": ">", "right": 50}], "exits": {"t1_r": 3, "t2_r": 2}})


def test_custom_exits_drive_levels(feats):
    from engine.config import EngineConfig
    from engine.levels import compute_levels

    spec = build_custom_strategy({"name": "e", "direction": "LONG", "conditions": [{"left": "rsi", "op": ">", "right": 0}],
                                  "exits": {"stop_method": "atr", "atr_stop_mult": 1.0, "t1_r": 1.0, "t2_r": 2.0, "max_hold_bars": 7}})
    assert spec.max_hold_bars == 7
    lv = compute_levels(feats, len(feats) - 1, "LONG", spec.levels or EngineConfig().levels)
    assert lv.risk_per_unit == pytest.approx(lv.atr, abs=0.02)  # pure 1×ATR stop
    assert "ATR" in lv.stop_method


def test_dsl_signals_have_no_look_ahead():
    df = random_walk(600, seed=4)
    full, part = build_features(df), build_features(df.iloc[:551])
    spec = build_custom_strategy({"name": "la", "direction": "LONG", "conditions": [
        {"left": "close", "op": "crosses_above", "right": {"feature": "ema20", "mult": 1.01}},
        {"left": "rsi", "op": ">", "right": {"feature": "rsi", "shift": 3}}]})
    assert detect(spec, full).iloc[550] == detect(spec, part).iloc[550]


# ------------------------------------------------------------------ portfolio simulator
def _closes(sym, start, values):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), name=sym)


def _trade(sym, entry_date, exit_date, entry=100.0, stop=95.0, exit_price=110.0, t1=105.0, t1_date=None, score=70, reason="target2"):
    return {"symbol": sym, "strategy_id": "s", "direction": "LONG", "signal_date": entry_date, "entry_date": entry_date, "exit_date": exit_date,
            "entry": entry, "stop": stop, "t1": t1, "exit_price": exit_price, "t1_date": t1_date, "exit_reason": reason, "score_at_signal": score}


NO_COST = dict(commission_pct=0.0, slippage_pct=0.0)


def test_sizing_by_risk_and_pnl():
    closes = {"A": _closes("A", "2024-01-01", [100, 102, 104, 106, 110])}
    r = simulate([_trade("A", "2024-01-01", "2024-01-05")], closes, PortfolioConfig(initial_capital=100_000, risk_per_trade_pct=1, partial_at_t1=0, **NO_COST))
    # risk budget 1,000 / 5 per share = 200 shares; capped by 20% position = 200 shares (20,000/100)
    assert r["trade_log"][0]["qty"] == 200 and r["final_equity"] == pytest.approx(102_000)


def test_max_positions_and_score_priority():
    days = ["2024-01-01"]
    closes = {s: _closes(s, "2024-01-01", [100] * 5) for s in "ABC"}
    trades = [_trade("A", days[0], "2024-01-05", score=60), _trade("B", days[0], "2024-01-05", score=90), _trade("C", days[0], "2024-01-05", score=80)]
    r = simulate(trades, closes, PortfolioConfig(max_positions=2, **NO_COST))
    assert {t["symbol"] for t in r["trade_log"]} == {"B", "C"} and r["skipped"]["max_positions"] == 1


def test_same_day_stop_resolves_and_frees_slot():
    closes = {s: _closes(s, "2024-01-01", [100, 96, 97, 98, 99]) for s in "AB"}
    trades = [_trade("A", "2024-01-01", "2024-01-01", exit_price=95.0, reason="stop"), _trade("B", "2024-01-02", "2024-01-05")]
    r = simulate(trades, closes, PortfolioConfig(max_positions=1, **NO_COST))
    assert r["open_at_end"] == 0 and len(r["trade_log"]) == 2 and not r["skipped"]


def test_partial_exit_at_t1_and_costs():
    closes = {"A": _closes("A", "2024-01-01", [100, 106, 108, 109, 110])}
    t = _trade("A", "2024-01-01", "2024-01-05", t1_date="2024-01-02")
    free = simulate([t], closes, PortfolioConfig(partial_at_t1=0.5, **NO_COST))
    q = free["trade_log"][0]["qty"]  # 1% of 10,00,000 / ₹5 risk = 2,000 shares
    assert q == 2000 and free["trade_log"][0]["pnl"] == pytest.approx(q / 2 * 5 + q / 2 * 10)  # half @105, half @110
    costly = simulate([t], closes, PortfolioConfig(partial_at_t1=0.5, commission_pct=0.1, slippage_pct=0.05))
    assert costly["final_equity"] < free["final_equity"] and costly["total_costs"] > 0


def test_mark_to_market_drawdown_is_intra_trade():
    closes = {"A": _closes("A", "2024-01-01", [100, 90, 80, 100, 110])}
    r = simulate([_trade("A", "2024-01-01", "2024-01-05", stop=99.0)], closes, PortfolioConfig(partial_at_t1=0, **NO_COST))
    # 2,000 shares (20% cap) marked at 80 → −₹40,000 on ₹10 lakh: open loss is visible before the winning exit
    assert r["max_drawdown_pct"] == pytest.approx(-4.0, abs=0.01) and r["final_equity"] > 1_000_000


# ------------------------------------------------------------------ explorer
def test_explore_grouping_and_filters():
    ev = pd.DataFrame([{"symbol": s, "strategy_id": "x", "signal_date": f"{y}-03-01", "exit_date": f"{y}-03-10", "t1_hit": h, "t2_hit": False,
                        "stop_hit": not h, "neither": False, "net_return_pct": 2 if h else -1, "r_multiple": 1 if h else -1, "bars_held": 5,
                        "mfe_r": 1.2 if h else 0.3, "mae_r": 0.2 if h else 1.0, "regime": "Weak Bull", "score_bucket": "75+", "direction": "LONG",
                        "exit_reason": "target2" if h else "stop"}
                       for y, s, h in [(2021, "A", True), (2021, "B", False), (2022, "A", True), (2022, "A", True)]])
    out = explore(ev, group_by="year", sectors={"A": "IT", "B": "Auto"})
    assert [g["key"] for g in out["groups"]] == ["2021", "2022"] and out["groups"][1]["t1_hit_rate"] == 100.0
    it = explore(ev, sector="IT", sectors={"A": "IT", "B": "Auto"})
    assert it["overall"]["sample_size"] == 3 and it["distributions"]["pct_reaching_1r_mfe"] == 100.0
    with pytest.raises(ValueError):
        explore(ev, group_by="password")


# ------------------------------------------------------------------ API
DEFN = {"name": "Vol breakout v", "direction": "LONG",
        "conditions": [{"left": "close", "op": ">", "right": "hh20_prior"}, {"left": "volume", "op": ">", "right": {"feature": "vol_sma20", "mult": 1.3}}],
        "exits": {"stop_method": "structure", "atr_stop_mult": 2.0, "t1_r": 1.5, "t2_r": 3.0, "max_hold_bars": 15}}


@pytest.fixture(scope="module")
def analyst(app_client, admin_headers):
    return make_user(app_client, "analyst3@example.com", role="analyst", admin_headers=admin_headers)


def test_strategy_versioning_flow(app_client, analyst, scanned):
    r = app_client.post(f"{API}/strategies", json={**DEFN, "key": "vol_bo_v", "note": "first"}, headers=analyst)
    assert r.status_code == 201, r.text
    assert r.json()["current_version"] == 1 and r.json()["conditions"][1] == "volume > 1.3×vol_sma20"
    v2 = {**DEFN, "exits": {**DEFN["exits"], "t2_r": 4.0}, "note": "wider T2"}
    r = app_client.put(f"{API}/strategies/vol_bo_v", json=v2, headers=analyst)
    assert r.json()["current_version"] == 2 and len(r.json()["versions"]) == 2
    old = app_client.get(f"{API}/strategies/vol_bo_v/versions/1", headers=analyst).json()
    assert old["definition"]["exits"]["t2_r"] == 3.0  # history is immutable
    bad = app_client.put(f"{API}/strategies/vol_bo_v", json={**DEFN, "conditions": [{"left": "x", "op": ">", "right": 1}]}, headers=analyst)
    assert bad.status_code == 422
    val = app_client.post(f"{API}/strategies/validate", json=DEFN, headers=analyst).json()
    assert val["valid"] and "T2 3R" in val["target_rule"]
    std = make_user(app_client, "std3@example.com")
    assert app_client.put(f"{API}/strategies/vol_bo_v", json=v2, headers=std).status_code == 403


def test_backtest_pinned_version_portfolio_compare_csv(app_client, admin_headers, analyst, scanned):
    ids = []
    for version in (1, 2):
        r = app_client.post(f"{API}/backtests", json={"strategy_key": "vol_bo_v", "strategy_version": version, "walk_forward": False,
                                                      "portfolio": {"initial_capital": 500000, "max_positions": 5}}, headers=analyst)
        assert r.status_code == 202, r.text
        bt = app_client.get(f"{API}/backtests/{r.json()['id']}", headers=analyst).json()
        assert bt["status"] == "done", bt["error"]
        ids.append(bt["id"])
        res = bt["result"]
        assert res["strategy"]["target_rule"].startswith(f"T1 1.5R / T2 {3 if version == 1 else 4}R")
        pf = res["portfolio"]
        assert pf["config"]["initial_capital"] == 500000 and pf["trades_taken"] + sum(pf["skipped"].values()) == pf["trades_available"]
        assert pf["max_concurrent_positions"] <= 5 and pf["open_at_end"] == 0 and pf["equity_curve"]
        assert pf["trade_log_total"] == pf["trades_taken"] and pf["trade_log_truncated"] is False
        assert "yearly_stability" in res
    cmp_ = app_client.get(f"{API}/backtests/compare/summary?ids={ids[0]},{ids[1]}", headers=analyst).json()
    assert [c["id"] for c in cmp_["items"]] == ids and [c["strategy_version"] for c in cmp_["items"]] == [1, 2]
    listed = {b["id"]: b for b in app_client.get(f"{API}/backtests", headers=analyst).json()["items"]}
    assert listed[ids[1]]["strategy_version"] == 2 and listed[ids[1]]["has_portfolio"] is True
    csv_ = app_client.get(f"{API}/backtests/{ids[0]}/trades.csv", headers=analyst)
    assert csv_.status_code == 200 and csv_.headers["content-type"].startswith("text/csv") and csv_.text.startswith("symbol,")
    assert app_client.get(f"{API}/backtests/compare/summary?ids={ids[0]},{ids[1]}", headers=admin_headers).status_code == 200
    other = make_user(app_client, "prem3@example.com", role="premium", admin_headers=admin_headers)
    assert app_client.get(f"{API}/backtests/{ids[0]}/trades.csv", headers=other).status_code == 404


def test_include_in_scan_publishes_custom_setups(app_client, admin_headers, analyst, scanned):
    assert app_client.patch(f"{API}/strategies/vol_bo_v", json={"include_in_scan": True}, headers=analyst).status_code == 200
    r = app_client.post(f"{API}/admin/jobs/scan", headers=admin_headers)
    assert app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()["status"] == "done"
    perf = app_client.get(f"{API}/strategies/vol_bo_v/performance", headers=analyst).json()
    assert perf["summary"]["sample_size"] > 0 and perf["strategy"]["notes"] == "Custom strategy v2"
    hr = app_client.get(f"{API}/analytics/hit-rates?strategy=vol_bo_v&group_by=year", headers=admin_headers).json()
    assert hr["overall"]["sample_size"] == perf["summary"]["sample_size"] and hr["groups"]
    app_client.patch(f"{API}/strategies/vol_bo_v", json={"include_in_scan": False}, headers=analyst)


def test_hit_rate_explorer_api(app_client, admin_headers, scanned):
    out = app_client.get(f"{API}/analytics/hit-rates?group_by=regime", headers=admin_headers).json()
    assert out["overall"]["sample_size"] > 0 and sum(g["sample_size"] for g in out["groups"]) <= out["overall"]["sample_size"]
    assert "r_multiple" in out["distributions"]
    assert app_client.get(f"{API}/analytics/hit-rates?group_by=bogus", headers=admin_headers).status_code == 422
    std = make_user(app_client, "std4@example.com")
    assert app_client.get(f"{API}/analytics/hit-rates", headers=std).status_code == 403
