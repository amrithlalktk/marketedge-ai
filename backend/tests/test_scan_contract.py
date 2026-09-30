"""Replays the scanner over recent sessions of the SAMPLE universe so the setup
contract is checked on real VALID outputs, not vacuously on an empty list."""
from __future__ import annotations

from datetime import date

import pytest

from app.providers.sample import BENCHMARK, VIX_SYMBOL, SampleUniverse
from engine.analyzer import Analyzer, MarketContext
from engine.regime import describe


@pytest.fixture(scope="module")
def replay():
    u = SampleUniverse(n_stocks=40, today=date(2026, 9, 29))
    a = Analyzer()
    feats = a.prepare({i.symbol: u.bars[i.symbol] for i in u.instruments if not i.is_index})
    ctx = a.market_context(u.bars[BENCHMARK], feats, vix=u.bars[VIX_SYMBOL]["close"])
    events = a.build_events(feats, ctx.regime_df, universe_dates={i.symbol: (i.listed_on, i.delisted_on) for i in u.instruments})
    inst = {i.symbol: i.to_dict() for i in u.instruments}
    out = []
    for d in feats["DEMO_001"].index[-40:]:
        fs = {s: f.loc[:d] for s, f in feats.items() if d in f.index}
        reg = ctx.regime_df.loc[:d]
        c = MarketContext(reg, describe(reg, u.bars[BENCHMARK]["close"].loc[:d]), ctx.breadth_df, {})
        out.append(a.scan(fs, events, c, today=d.date(), instruments=inst, meta={s: {"is_sample": True} for s in fs}))
    return out


def test_valid_setups_exist_and_carry_evidence(replay):
    valid = [s for r in replay for s in r["valid"]]
    assert valid, "expected at least one VALID setup across 40 sample sessions"
    for s in valid:
        p = s["probability"]
        assert p["sample_size"] >= 30 and p["backtest_period"] and p["conditioning"]
        assert pd_before(p["backtest_period"][1], s["as_of"]), "historical evidence must end before the setup date"
        assert s["rr_t2"] >= 2.0 and p["expectancy_r"] > 0 and s["score"] >= 60
        assert all(c["passed"] or c["severity"] == "warn" for c in s["checks"])
        assert s["explanation"]["agreeing"] and s["explanation"]["invalidation"]
        assert "do not guarantee" in s["disclaimer"]


def test_no_trade_always_states_reasons(replay):
    rejected = [s for r in replay for s in r["no_trade"]]
    assert rejected
    for s in rejected:
        assert any(not c["passed"] and c["severity"] == "block" for c in s["checks"])


def test_days_without_valid_setups_say_so(replay):
    for r in replay:
        if not r["valid"]:
            assert r["market_message"]


def pd_before(a: str, b: str) -> bool:
    return a < b
