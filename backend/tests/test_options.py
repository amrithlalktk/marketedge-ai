"""Phase 2: options pricing, chain analytics, strategy payoffs, contract selection and API."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from engine.options import chain as ch
from engine.options.pricing import IST, black76, forward, greeks, implied_vol, year_fraction
from engine.options.selector import OptionsConfig, find_option_setups
from engine.options.strategies import Leg, analyze_structure, build_strategies

API = "/api/v1"
R, Q = 0.065, 0.012


# ------------------------------------------------------------------ pricing
def test_black76_known_value_and_put_call_parity():
    F, K, T, v = 100.0, 100.0, 0.5, 0.2
    c = float(black76(F, K, T, v, 0.05, True))
    p = float(black76(F, K, T, v, 0.05, False))
    assert c == pytest.approx(5.4980, abs=1e-3)  # e^{-rT}·F·(N(d1)−N(−d1)), d1 = σ√T/2
    assert c - p == pytest.approx(np.exp(-0.05 * T) * (F - K), abs=1e-9)
    F2 = 110
    assert float(black76(F2, K, T, v, 0.05, True)) - float(black76(F2, K, T, v, 0.05, False)) == pytest.approx(np.exp(-0.025) * 10, abs=1e-9)


def test_implied_vol_round_trip():
    K = np.array([90.0, 100.0, 110.0])
    for is_call in (True, False):
        px = black76(100.0, K, 0.25, np.array([0.3, 0.22, 0.18]), R, is_call)
        iv = implied_vol(px, 100.0, K, 0.25, R, is_call)
        assert np.allclose(iv, [0.3, 0.22, 0.18], atol=1e-6)
    assert np.isnan(implied_vol(np.array([0.001]), 100.0, np.array([50.0]), 0.25, R, True))[0]  # below intrinsic


def test_greeks_match_finite_differences():
    S, K, T, v = 25000.0, 25100.0, 10 / 365, 0.15
    price = lambda s, t=T, vol=v: float(black76(forward(s, R, Q, t), K, t, vol, R, True))
    g = greeks(S, K, T, v, R, Q, True)
    h = 1.0
    assert float(g["delta"]) == pytest.approx((price(S + h) - price(S - h)) / (2 * h), rel=1e-3)
    assert float(g["gamma"]) == pytest.approx((price(S + h) - 2 * price(S) + price(S - h)) / h ** 2, rel=2e-2)
    assert float(g["vega"]) == pytest.approx((price(S, vol=v + 0.005) - price(S, vol=v - 0.005)) / 1.0, rel=1e-3)
    dt = 1 / 365
    assert float(g["theta"]) == pytest.approx(price(S, t=T - dt) - price(S), rel=5e-2)


def test_year_fraction_to_1530_ist():
    as_of = datetime(2026, 9, 29, 15, 30, tzinfo=IST)
    assert year_fraction(as_of, date(2026, 10, 6)) == pytest.approx(7 / 365)
    assert year_fraction(as_of, date(2026, 9, 29)) == 0.0


# ------------------------------------------------------------------ chain analytics
def _toy_chain(expiry=date(2026, 10, 6)):
    rows = []
    for k, coi, poi in [(24800, 100, 900), (24900, 200, 1500), (25000, 1000, 1000), (25100, 1600, 300), (25200, 800, 100)]:
        rows.append((expiry, k, "CE", 1.0, 1.1, 1.05, 10, coi, 5))
        rows.append((expiry, k, "PE", 1.0, 1.1, 1.05, 10, poi, 5))
    df = pd.DataFrame(rows, columns=["expiry", "strike", "option_type", "bid", "ask", "ltp", "volume", "oi", "oi_change"])
    df["is_call"] = df["option_type"] == "CE"
    return df


def test_max_pain_hand_computed():
    c = _toy_chain()
    strikes = sorted(c["strike"].unique())
    calls, puts = c[c.is_call], c[~c.is_call]
    pain = {s: sum(max(s - k, 0) * o for k, o in zip(calls.strike, calls.oi)) + sum(max(k - s, 0) * o for k, o in zip(puts.strike, puts.oi)) for s in strikes}
    assert ch.max_pain(c, date(2026, 10, 6)) == min(pain, key=pain.get)


def test_pcr_and_oi_walls():
    c = _toy_chain()
    p = ch.pcr(c, date(2026, 10, 6))
    assert p["pcr_oi"] == pytest.approx(3800 / 3700, abs=1e-3)
    lv = ch.oi_levels(c, date(2026, 10, 6), spot=25020)
    assert lv["support"][0]["strike"] == 24900 and lv["resistance"][0]["strike"] == 25100


def test_oi_buildup_interpretation():
    c = _toy_chain()
    prev = pd.Series({(e, float(k), t): 0.5 for e, k, t in zip(c.expiry, c.strike, c.option_type)})
    out = ch.oi_buildup(c, date(2026, 10, 6), prev)
    assert out and all(x["interpretation"] == "Long buildup" for x in out)  # price up + OI up


def test_iv_percentile_requires_history():
    assert ch.iv_percentile(pd.Series(np.linspace(0.1, 0.2, 30)), 0.15) is None
    assert ch.iv_percentile(pd.Series(np.linspace(0.1, 0.2, 100)), 0.15) == pytest.approx(50, abs=2)


# ------------------------------------------------------------------ payoffs
def _leg(t, k, side, prem):
    return Leg(t, k, "2026-10-06", side, 1, prem, 0.15, 0, 0, 0, 0)


def test_bull_call_spread_payoff_metrics():
    legs = [_leg("CE", 25000, 1, 200.0), _leg("CE", 25200, -1, 110.0)]
    a = analyze_structure(legs, 25000, 25030, 7 / 365, 0.15, 75, None, 5)
    assert a["max_loss"] == pytest.approx(-90 * 75) and a["max_profit"] == pytest.approx(110 * 75)
    assert a["breakevens"] == [pytest.approx(25090, abs=0.5)]
    assert a["premium_type"] == "debit" and not a["max_loss_unlimited"]


def test_short_straddle_unlimited_and_two_breakevens():
    legs = [_leg("CE", 25000, -1, 250.0), _leg("PE", 25000, -1, 240.0)]
    a = analyze_structure(legs, 25000, 25030, 7 / 365, 0.15, 75, None, 5)
    assert a["max_loss_unlimited"] and a["max_profit"] == pytest.approx(490 * 75)
    assert a["breakevens"] == [pytest.approx(24510, abs=0.5), pytest.approx(25490, abs=0.5)]
    assert 0 < a["pop_model"] < 100


def test_long_put_bounded_profit():
    a = analyze_structure([_leg("PE", 25000, 1, 200.0)], 25000, 25030, 7 / 365, 0.15, 75, None, 5)
    assert not a["max_profit_unlimited"] and a["max_profit"] == pytest.approx((25000 - 0.01 - 200) * 75, rel=1e-4)


def test_historical_pop_has_sample_size():
    idx = pd.bdate_range("2018-01-01", periods=800)
    closes = pd.Series(25000 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, 800))), index=idx)
    a = analyze_structure([_leg("CE", 25000, 1, 200.0)], 25000, 25030, 7 / 365, 0.15, 75, closes, 5)
    h = a["pop_historical"]
    assert h["horizon_sessions"] == 5 and h["all"]["sample_size"] == 795 and 0 <= h["all"]["pop"] <= 100


# ------------------------------------------------------------------ selector & strategy gating
@pytest.fixture(scope="module")
def sample_chain():
    from app.providers.sample_options import synthetic_chain

    as_of = datetime(2026, 9, 29, 15, 30, tzinfo=IST)
    raw = synthetic_chain(25000.0, 0.14, as_of, seed=3)
    return ch.enrich(raw, 25000.0, as_of, ch.ChainConfig(), 75), as_of


def _und(direction="LONG", status="VALID", score=80.0, t1=25250.0, t2=25500.0, stop=24850.0):
    return {"symbol": "X", "direction": direction, "status": status, "score": score, "current_price": 25000.0, "entry_zone": [25000, 25050],
            "stop": stop, "targets": [t1, t2, 26000.0], "rr_t2": 3.3, "stop_method": "m", "target_methods": ["a", "b", "c"],
            "strategy": {"id": "idx_breakout", "name": "Index Breakout"}, "expected_holding_days": 2.0, "as_of": "2026-09-29",
            "probability": {"t1_hit_rate": 55.0, "sample_size": 60, "backtest_period": ["2019-01-01", "2026-09-01"]},
            "checks": [], "explanation": {"agreeing": ["x"], "risk_factors": []}}


def test_chain_enrich_recovers_generated_iv(sample_chain):
    c, _ = sample_chain
    liquid = c[c["liquid"]]
    assert len(liquid) > 50 and liquid["iv"].between(0.05, 0.6).all()
    assert (c[c.is_call]["delta"].between(0, 1)).all() and (c[~c.is_call]["delta"].between(-1, 0)).all()


def test_selector_picks_liquid_contract_in_delta_band(sample_chain):
    c, _ = sample_chain
    out = find_option_setups(c, 25000.0, [_und()], 40.0, 0.14, 75, ch.ChainConfig(), OptionsConfig())
    s = out[0]
    assert s["contract"]["option_type"] == "CE" and 0.25 <= abs(s["contract"]["delta"]) <= 0.75
    assert s["contract"]["dte"] >= 3
    assert s["probability"]["basis"] == "underlying" and "Historical option prices were not used" in s["probability"]["basis_note"]
    assert s["stop"] < s["entry"] < s["targets"][0] < s["targets"][1]


def test_selector_blocks_when_iv_too_rich_or_underlying_invalid(sample_chain):
    c, _ = sample_chain
    rich = find_option_setups(c, 25000.0, [_und()], 95.0, 0.14, 75, ch.ChainConfig(), OptionsConfig())[0]
    assert rich["status"] == "NO_TRADE"
    weak = find_option_setups(c, 25000.0, [_und(status="NO_TRADE")], 40.0, 0.14, 75, ch.ChainConfig(), OptionsConfig())[0]
    assert weak["status"] == "NO_TRADE"


def test_selector_rejects_illiquid(sample_chain):
    c, _ = sample_chain
    dry = c.copy()
    dry["liquid"] = False
    out = find_option_setups(dry, 25000.0, [_und()], 40.0, 0.14, 75, ch.ChainConfig(), OptionsConfig())[0]
    assert out["status"] == "NO_TRADE" and any(x["name"] == "Contract liquidity" and not x["passed"] for x in out["checks"])


def _state(direction, iv_vol="Normal volatility", adx=15.0, events=(), squeeze=False):
    return {"direction": direction, "events": list(events), "volatility": iv_vol, "squeeze": squeeze, "adx": adx}


def test_strategy_gating(sample_chain):
    c, _ = sample_chain
    oi = ch.oi_levels(c, sorted(c["expiry"].unique())[1], 25000.0)
    atm = {e: ch.atm_iv(c, e) for e in c["expiry"].unique()}
    args = dict(chain=c, spot=25000.0, atm_iv_by_expiry=atm, oi=oi, lot_size=75, chain_cfg=ch.ChainConfig(), hist_closes=None, regime_mask=None, sessions_to={})
    rng = build_strategies(state=_state("Range-bound"), iv_pct=80.0, regime={"regime": "Range"}, underlying_setups=[], **args)
    names = {p["key"] for p in rng["proposed"]}
    assert "short_strangle" in names or "short_straddle" in names
    assert not names & {"long_call", "long_put", "long_straddle"}
    panic = build_strategies(state=_state("Range-bound"), iv_pct=80.0, regime={"regime": "Panic/Selloff"}, underlying_setups=[], **args)
    assert not {p["key"] for p in panic["proposed"]} & {"short_straddle", "short_strangle"}
    bull = build_strategies(state=_state("Bullish", adx=28), iv_pct=40.0, regime={"regime": "Strong Bull"}, underlying_setups=[_und()], **args)
    bn = {p["key"] for p in bull["proposed"]}
    assert "long_call" in bn and "bull_call_spread" in bn and "short_straddle" not in bn
    for p in bull["proposed"] + rng["proposed"]:
        assert p["breakevens"] and (p["required_capital"] or 0) > 0 and p["pop_model"] is not None
    unknown_iv = build_strategies(state=_state("Bullish", adx=28), iv_pct=None, regime={"regime": "Strong Bull"}, underlying_setups=[_und()], **args)
    assert not {p["key"] for p in unknown_iv["proposed"]}, "no IV-dependent strategy without an IV percentile"


# ------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def options_ready(app_client, admin_headers, scanned):
    r = app_client.post(f"{API}/admin/jobs/options", headers=admin_headers)
    assert r.status_code == 202
    job = app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()
    assert job["status"] == "done", job
    return job


def test_options_overview_and_chain(app_client, admin_headers, options_ready):
    o = app_client.get(f"{API}/options/nifty", headers=admin_headers).json()
    assert o["data"]["is_sample"] is True and "entire premium" in o["disclaimer"]
    assert o["market_state"]["direction"] in ("Bullish", "Bearish", "Range-bound")
    assert o["oi"]["max_pain"] and o["oi"]["pcr_oi"] and o["iv"]["iv_percentile"] is not None
    assert o["intraday"] and set(o["intraday"]["timeframes"]) == {"5M", "15M", "30M", "1H"}
    chain = app_client.get(f"{API}/options/nifty/chain?width=5", headers=admin_headers).json()
    assert len(chain["rows"]) == 11 and "CE" in chain["rows"][5] and "delta" in chain["rows"][5]["CE"]
    far = o["expiries"][-1]
    assert app_client.get(f"{API}/options/nifty/chain?expiry={far}&width=5", headers=admin_headers).json()["expiry"] == far


def test_option_signals_and_strategies_permissions(app_client, admin_headers, options_ready):
    from tests.conftest import make_user

    std = make_user(app_client, "optstd@example.com")
    assert app_client.get(f"{API}/options/nifty", headers=std).status_code == 200
    assert app_client.get(f"{API}/options/signals", headers=std).status_code == 403
    sig = app_client.get(f"{API}/options/signals", headers=admin_headers).json()
    for s in sig["items"]:
        if "contract" in s:
            assert s["probability"]["basis"] == "underlying" and s["checks"]
    st = app_client.get(f"{API}/options/strategies", headers=admin_headers).json()
    assert len(st["proposed"]) + len(st["not_suitable"]) == 10
    for n in st["not_suitable"]:
        assert n["failed"]
    top = app_client.get(f"{API}/signals/top?limit=100", headers=admin_headers).json()
    assert all(i["market"] == "NSE" for i in top["items"])  # option setups never leak into equity lists


def test_payoff_builder(app_client, admin_headers, options_ready):
    ch_ = app_client.get(f"{API}/options/nifty/chain?width=5", headers=admin_headers).json()
    k = ch_["atm_strike"]
    legs = [{"strike": k, "option_type": "CE", "expiry": ch_["expiry"], "side": "BUY"},
            {"strike": k + 200, "option_type": "CE", "expiry": ch_["expiry"], "side": "SELL"}]
    r = app_client.post(f"{API}/options/payoff", json={"legs": legs}, headers=admin_headers)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["premium_type"] == "debit" and not a["max_loss_unlimited"] and len(a["breakevens"]) == 1
    assert a["required_capital"] > 0 and a["reward_to_risk"] and a["as_of"].endswith("+05:30") and a["legs"][0]["iv"] > 1
    bad = app_client.post(f"{API}/options/payoff", json={"legs": [{**legs[0], "strike": 1.0}]}, headers=admin_headers)
    assert bad.status_code == 422




def test_payoff_curve_keeps_strike_kinks():
    legs = [_leg("CE", 25000, 1, 200.0), _leg("CE", 25200, -1, 110.0)]
    a = analyze_structure(legs, 25000, 25030, 7 / 365, 0.15, 75, None, 5)
    xs = [x for x, _ in a["payoff_curve"]]
    assert any(abs(x - 25000) < 1 for x in xs) and any(abs(x - 25200) < 1 for x in xs)
    assert any(abs(x - a["breakevens"][0]) < 1 for x in xs)
    assert a["payoff_curve"] == sorted(a["payoff_curve"])



def test_oi_buildup_keys_include_expiry():
    """Same strike/type in two expiries must not collide (regression: ambiguous Series truth value)."""
    a, b = _toy_chain(date(2026, 10, 6)), _toy_chain(date(2026, 10, 13))
    c = pd.concat([a, b], ignore_index=True)
    prev = pd.Series({(e, float(k), t): (0.5 if e == date(2026, 10, 6) else 2.0) for e, k, t in zip(c.expiry, c.strike, c.option_type)})
    near = ch.oi_buildup(c, date(2026, 10, 6), prev)
    far = ch.oi_buildup(c, date(2026, 10, 13), prev)
    assert {x["interpretation"] for x in near} == {"Long buildup"} and {x["interpretation"] for x in far} == {"Short buildup"}


def test_option_ideas_are_tracked_and_shown_in_history(app_client, admin_headers, options_ready):
    """A published NIFTY option idea is tracked once and resolved on the NIFTY levels that triggered it."""
    from datetime import date

    import pandas as pd
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Instrument, ScanRun, Signal, SignalOutcome
    from app.services.options_service import track_option_outcomes
    from app.services.scan_service import resolve_outcomes
    from app.services.settings_service import engine_config
    from engine.features import build_features
    from tests.conftest import make_user

    idx = pd.bdate_range("2026-01-01", periods=90)
    close = pd.Series([100.0 + i for i in range(90)], index=idx)  # steady rise: the long trigger reaches both targets
    f = build_features(pd.DataFrame({"open": close - 0.5, "high": close + 0.5, "low": close - 1.0, "close": close, "volume": 1e6}, index=idx))
    t = 60
    c = float(close.iloc[t])
    db = SessionLocal()
    try:
        und = db.scalar(select(Instrument).where(Instrument.symbol == "DEMO_NIFTY50"))
        run = db.scalar(select(ScanRun).where(ScanRun.market == "NFO").order_by(ScanRun.id.desc()))
        payload = {"contract": {"label": "NIFTY 50 TEST CE"}, "entry": 120.0, "stop": 80.0, "targets": [150.0, 190.0],
                   "probability": {"t1_hit_rate": 52.0, "sample_size": 40},
                   "underlying": {"symbol": "DEMO_NIFTY50", "price": c, "strategy": {"id": "breakout_volume", "name": "Breakout"},
                                  "entry_zone": [c, c], "stop": c - 5, "targets": [c + 2, c + 4]}}
        for _ in range(2):  # a rescan of the same session republishes the idea
            db.add(Signal(scan_run_id=run.id, instrument_id=und.id, symbol="NIFTY 50 TEST CE", market="NFO", strategy_key="opt:breakout_volume",
                          direction="LONG", status="VALID", as_of=date.fromisoformat(str(idx[t].date())), score=60, rr_t2=2.0,
                          t1_hit_rate=52.0, sample_size=40, is_sample_data=True, payload=payload))
        db.flush()
        track_option_outcomes(db)
        assert track_option_outcomes(db) == 0
        ids = [i for (i,) in db.execute(select(Signal.id).where(Signal.symbol == "NIFTY 50 TEST CE"))]
        assert db.query(SignalOutcome).filter(SignalOutcome.signal_id.in_(ids)).count() == 1
        cfg = engine_config(db)
        assert resolve_outcomes(db, {"DEMO_NIFTY50": f}, cfg.backtest, cfg.levels.max_chase_atr, "NFO") >= 1
    finally:
        db.close()

    h = app_client.get(f"{API}/signals/history?market=NFO", headers=admin_headers).json()
    row = next(i for i in h["items"] if i["label"] == "NIFTY 50 TEST CE")
    assert row["result"] == "target2" and row["judged_on"]["symbol"] == "DEMO_NIFTY50" and row["entry_zone"] == [120.0, 120.0]
    assert row["move"]["of"] == "DEMO_NIFTY50" and row["move"]["price_now"] is not None
    assert h["summary"]["closed"] >= 1 and h["summary"]["target1_or_better"] >= 1
    std = make_user(app_client, "histstd@example.com")
    assert all(i["market"] != "NFO" for i in app_client.get(f"{API}/signals/history", headers=std).json()["items"])


def test_history_lists_stock_ideas(app_client, admin_headers, scanned):
    h = app_client.get(f"{API}/signals/history?market=NSE", headers=admin_headers).json()
    assert set(h["summary"]) >= {"ideas", "open", "closed", "target1_rate", "stop_rate", "expected_target1_rate"}
    for i in h["items"]:
        assert i["market"] == "NSE" and i["result"] in ("open", "target1", "target2", "stop", "time", "not_filled")
        m = i["move"]
        assert m["of"] == i["label"] and m["price_now"] is not None and m["change_pct"] == round(100 * (m["price_now"] / m["price_then"] - 1), 2)
    assert app_client.get(f"{API}/signals/history?market=XX", headers=admin_headers).status_code == 422


def test_no_trade_reason_says_why():
    from engine.options.analyzer import no_trade_reason

    assert "none of the NIFTY strategies triggered" in no_trade_reason([], [])
    u = {"status": "NO_TRADE", "direction": "LONG", "strategy": {"name": "Breakout"},
         "checks": [{"name": "Trend filter", "passed": False, "severity": "block"}, {"name": "Volume", "passed": False, "severity": "warn"}]}
    msg = no_trade_reason([u], [])
    assert "Breakout (long) failed Trend filter" in msg and "Volume" not in msg
    ok = {**u, "status": "VALID", "checks": []}
    msg = no_trade_reason([ok], [{"status": "NO_TRADE", "direction": "BULLISH", "reason": "No CE contract with 7–45 days to expiry in the delta band"}])
    assert "no option contract passed" in msg and "delta band" in msg


def test_options_report(app_client, options_ready):
    from app.cli import options_report
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        r = options_report(db)
    finally:
        db.close()
    assert r["status"] in ("VALID", "NO_TRADE") and isinstance(r["nifty_setups"], list) and isinstance(r["option_setups"], list)
    assert r["status"] == "VALID" or r["market_message"].startswith("NO TRADE:")
