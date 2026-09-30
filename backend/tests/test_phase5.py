"""Phase 5: sentiment, event risk, earnings reaction, calendar/news providers, options event gating,
AI analyst (fake client — never calls the real API), news/calendar/analyst API."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import numpy as np
import pandas as pd
import pytest

from engine.events import EventRiskConfig, earnings_reaction, macro_checks, news_context, relevant_events
from engine.sentiment import classify

API = "/api/v1"
NOW = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ sentiment
@pytest.mark.parametrize("title,label,cat", [
    ("Acme beats estimates, raises guidance", "Positive", "earnings"),
    ("Regulator opens probe into Acme accounting fraud", "Negative", "regulatory"),
    ("Brokerage downgrades Acme to underperform", "Negative", "analyst"),
    ("Acme schedules board meeting", "Neutral", "general"),
])
def test_sentiment_labels_and_categories(title, label, cat):
    r = classify(title)
    assert r["label"] == label and r["category"] == cat and r["method"] == "lexicon-v1"


def test_sentiment_negation_and_explainability():
    r = classify("Acme denies fraud allegations")
    assert "fraud (negated)" in r["terms"]["negative"] and r["label"] != "Negative"


# ------------------------------------------------------------------ event risk
def _ev(name, hours, country="US", impact="High", ccy="USD"):
    return {"name": name, "country": country, "currency": ccy, "impact": impact, "event_time": (NOW + timedelta(hours=hours)).isoformat()}


def test_macro_checks_block_vs_warn():
    evs = [_ev("FOMC", 10), _ev("CPI", 50), _ev("Claims", 5, impact="Low"), _ev("Old", -5)]
    fx = macro_checks(evs, NOW, True, EventRiskConfig())
    stock = macro_checks(evs, NOW, False, EventRiskConfig())
    assert [(c.name, c.severity) for c in fx] == [("Event risk (macro)", "block"), ("Macro event approaching", "warn")]
    assert all(c.severity == "warn" for c in stock) and len(stock) == 2


def test_relevant_events_by_market_and_currency():
    evs = [_ev("FOMC", 1), _ev("RBI", 1, "IN", ccy="INR"), _ev("ECB", 1, "EU", ccy="EUR"), _ev("BoJ", 1, "JP", ccy="JPY")]
    assert {e["name"] for e in relevant_events(evs, "NSE")} == {"FOMC", "RBI"}
    assert {e["name"] for e in relevant_events(evs, "FX", {"EUR", "JPY"})} == {"ECB", "BoJ"}
    assert {e["name"] for e in relevant_events(evs, "EUROPE", {"EUR"})} == {"ECB"}


def test_news_context_warns_only_when_news_opposes_direction():
    arts = [{"published_at": (NOW - timedelta(hours=h)).isoformat(), "sentiment_label": lab, "sentiment_score": sc, "title": "t"}
            for h, lab, sc in [(5, "Negative", -0.8), (20, "Negative", -0.6), (30, "Positive", 0.5), (400, "Negative", -0.9)]]
    long_ = news_context(arts, "LONG", NOW, EventRiskConfig())
    assert long_["articles"] == 3 and long_["counts"]["Negative"] == 2 and long_["checks"][0]["severity"] == "warn"
    assert news_context(arts, "SHORT", NOW, EventRiskConfig())["checks"] == []
    assert "never generates" in long_["note"]


def test_earnings_reaction_measures_event_moves():
    idx = pd.bdate_range("2025-01-01", periods=300)
    close = pd.Series(100 * np.exp(np.cumsum(np.full(300, 0.001))), index=idx)
    ev_dates = [idx[100], idx[200]]
    for d in ev_dates:
        close.loc[d:] *= 1.08  # 8% jump on each report
    r = earnings_reaction(close, ev_dates)
    assert r["count"] == 2 and r["avg_abs_move_pct"] > 7 and r["vs_typical"] > 5


# ------------------------------------------------------------------ providers
def test_finnhub_adapter_mocked():
    from app.providers.finnhub import FinnhubProvider, ProviderUnavailable

    def handler(req: httpx.Request):
        p = req.url.path
        if p.endswith("/company-news"):
            return httpx.Response(200, json=[{"id": 1, "datetime": 1790000000, "headline": "Acme beats estimates", "summary": "", "url": "u", "source": "X"}])
        if p.endswith("/news"):
            return httpx.Response(200, json=[])
        if p.endswith("/calendar/earnings"):
            return httpx.Response(200, json={"earningsCalendar": [{"symbol": "RELIANCE.NS", "date": "2026-10-20", "quarter": 2, "year": 2026, "hour": "amc",
                                                                   "epsEstimate": 30.1, "epsActual": None, "revenueEstimate": 1e11, "revenueActual": None}]})
        if p.endswith("/calendar/economic"):
            return httpx.Response(403, json={"error": "premium"})
        return httpx.Response(404)

    f = FinnhubProvider("K", {"RELIANCE": "RELIANCE.NS"}, httpx.Client(transport=httpx.MockTransport(handler)), rate_per_min=60000)
    news = f.get_news(["RELIANCE"], NOW - timedelta(days=3), "NSE")
    assert news[0]["symbols"] == ["RELIANCE"] and news[0]["title"] == "Acme beats estimates" and not news[0]["is_sample"]
    earn = f.get_earnings(["RELIANCE"], date(2026, 10, 1), date(2026, 10, 31))
    assert earn[0]["symbol"] == "RELIANCE" and earn[0]["time"] == "amc"
    with pytest.raises(ProviderUnavailable):
        f.get_events(date(2026, 10, 1), date(2026, 10, 31))
    with pytest.raises(ProviderUnavailable):
        FinnhubProvider(None).get_news(["X"], NOW, "NSE")


def test_sample_calendar_is_labelled_and_consistent():
    from app.providers.sample_events import SampleCalendarProvider

    c = SampleCalendarProvider()
    evs = c.get_events(date(2026, 9, 1), date(2026, 12, 31))
    assert evs and all(e["is_sample"] and "(sample)" in e["name"] and e["impact"] in ("Low", "Medium", "High") for e in evs)
    earn = c.get_earnings(["DEMO_001"], date(2025, 1, 1), date(2026, 12, 31))
    gaps = np.diff([date.fromisoformat(e["event_date"]).toordinal() for e in earn])
    assert (gaps == 91).all()  # one quarterly cycle per company


# ------------------------------------------------------------------ options: short vol blocked before a high-impact release
def test_short_vol_blocked_by_event_before_expiry():
    from app.providers.sample_options import synthetic_chain
    from engine.options import chain as ch
    from engine.options.pricing import IST
    from engine.options.strategies import build_strategies

    as_of = datetime(2026, 9, 29, 15, 30, tzinfo=IST)
    c = ch.enrich(synthetic_chain(25000.0, 0.14, as_of, seed=3), 25000.0, as_of, ch.ChainConfig(), 75)
    near = sorted(e for e in c["expiry"].unique() if 3 <= (e - as_of.date()).days <= 10)[0]
    args = dict(chain=c, spot=25000.0, iv_pct=80.0, atm_iv_by_expiry={e: ch.atm_iv(c, e) for e in c["expiry"].unique()},
                regime={"regime": "Range"}, oi=ch.oi_levels(c, near, 25000.0), underlying_setups=[], lot_size=75, chain_cfg=ch.ChainConfig(),
                hist_closes=None, regime_mask=None, sessions_to={},
                state={"direction": "Range-bound", "events": [], "volatility": "High volatility", "squeeze": False, "adx": 15.0})
    free = {p["key"] for p in build_strategies(**args)["proposed"]}
    blocked = build_strategies(**args, high_events_before={near: ["RBI Monetary Policy Decision"]})
    assert free & {"short_straddle", "short_strangle"}
    assert not {p["key"] for p in blocked["proposed"]} & {"short_straddle", "short_strangle"}
    why = next(n for n in blocked["not_suitable"] if n["key"] == "short_straddle")["failed"]
    assert any("RBI Monetary Policy Decision" in f for f in why)


# ------------------------------------------------------------------ AI analyst (fake client; no network, no cost)
class FakeClient:
    def __init__(self, text=None, stop="end_turn"):
        self.text, self.stop, self.calls = text, stop, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=1200, output_tokens=300, cache_read_input_tokens=900, cache_creation_input_tokens=0)
        content = [] if self.stop == "refusal" else [SimpleNamespace(type="text", text=self.text)]
        return SimpleNamespace(stop_reason=self.stop, model=kw["model"], usage=usage, content=content)


def test_grounding_check_flags_invented_numbers():
    from app.services.analyst_service import grounding_check

    ctx = {"setup": {"stop": 117.559, "targets": [99.79, 87.323], "historical_evidence": {"sample_size": 51, "t1_hit_rate": 45.1, "stop_rate": 0.43}}}
    ok = grounding_check("Stop at 117.56, T1 99.79; 51 similar trades, T1 hit rate 45.1% and 43% stopped. 1. Trigger", ctx)
    assert ok == []
    bad = grounding_check("Target 150.25 is likely with 88% probability", ctx)
    assert set(bad) == {"150.25", "88"}


@pytest.fixture(scope="module")
def p5(app_client, admin_headers, scanned):
    for path in ("/admin/jobs/calendar", "/admin/jobs/news?market=NSE"):
        r = app_client.post(API + path, headers=admin_headers)
        job = app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()
        assert job["status"] == "done", job
    r = app_client.post(f"{API}/admin/jobs/scan?market=NSE", headers=admin_headers)
    assert app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()["status"] == "done"
    return True


def test_news_and_calendar_api(app_client, admin_headers, p5):
    n = app_client.get(f"{API}/news?market=NSE&limit=200", headers=admin_headers).json()
    assert n["items"] and all(a["title"].startswith("[SAMPLE]") and a["is_sample"] for a in n["items"]) and "never generates" in n["note"]
    one = app_client.get(f"{API}/news/{n['items'][0]['id']}", headers=admin_headers).json()
    assert one["sentiment_terms"] is not None and one["sentiment_method"] == "lexicon-v1"
    neg = app_client.get(f"{API}/news?sentiment=Negative&days=30", headers=admin_headers).json()["items"]
    assert all(a["sentiment_label"] == "Negative" for a in neg)
    eco = app_client.get(f"{API}/calendar/economic?impact=High", headers=admin_headers).json()
    assert eco["items"] and all(e["impact"] == "High" for e in eco["items"])
    earn = app_client.get(f"{API}/calendar/earnings?market=NSE", headers=admin_headers).json()
    assert earn["upcoming"] and all(u["days_until"] >= 0 for u in earn["upcoming"])
    rec = [r for r in earn["recent"] if r["reported"]]
    assert rec and rec[0]["eps_surprise_pct"] is not None


def test_stock_events_and_setup_event_context(app_client, admin_headers, p5):
    ev = app_client.get(f"{API}/stocks/DEMO_001/events", headers=admin_headers).json()
    assert ev["earnings_history"] and ev["earnings_reaction"]["count"] >= 1 and ev["next_earnings"]["days_until"] >= 0
    for s in app_client.get(f"{API}/signals?status=NO_TRADE", headers=admin_headers).json()["items"]:
        full = app_client.get(f"{API}/signals/{s['id']}", headers=admin_headers).json()
        assert "events" in full and "news" in full and "never generates" in full["news"]["note"]


def test_analyst_llm_path_grounding_and_audit(app_client, admin_headers, p5, monkeypatch):
    from app.services import analyst_service

    sid = app_client.get(f"{API}/signals?status=NO_TRADE", headers=admin_headers).json()["items"][0]["id"]
    sig = app_client.get(f"{API}/signals/{sid}", headers=admin_headers).json()
    fake = FakeClient(f"Stop {sig['stop']} and T1 {sig['targets'][0]}. Historical performance does not guarantee future results.")
    monkeypatch.setattr(analyst_service, "_api_key", lambda: "test-key")
    monkeypatch.setattr(analyst_service, "_default_client", lambda key: fake)
    r = app_client.post(f"{API}/analyst/ask", json={"signal_id": sid, "question": "Why is this setup appearing?"}, headers=admin_headers).json()
    assert r["mode"] == "llm" and r["grounded"] and r["usage"]["cache_read_input_tokens"] == 900
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"} and "<context>" in call["messages"][0]["content"]
    assert call["output_config"] == {"effort": "medium"}
    bad = FakeClient("This will hit 9999.99 for sure")
    monkeypatch.setattr(analyst_service, "_default_client", lambda key: bad)
    r2 = app_client.post(f"{API}/analyst/ask", json={"signal_id": sid}, headers=admin_headers).json()
    assert r2["grounded"] is False and "9999.99" in r2["unverified_numbers"]
    hist = app_client.get(f"{API}/analyst/history", headers=admin_headers).json()["items"]
    assert hist[0]["grounded"] is False and hist[1]["grounded"] is True


def test_analyst_refusal_and_no_key_fallbacks(app_client, admin_headers, p5, monkeypatch):
    from app.services import analyst_service

    monkeypatch.setattr(analyst_service, "_api_key", lambda: "k")
    monkeypatch.setattr(analyst_service, "_default_client", lambda key: FakeClient(stop="refusal"))
    r = app_client.post(f"{API}/analyst/ask", json={"symbol": "DEMO_001"}, headers=admin_headers).json()
    assert r["mode"] == "rule_based" and "declined" in r["note"] and "does not guarantee" in r["answer"]
    monkeypatch.setattr(analyst_service, "_api_key", lambda: None)
    r = app_client.post(f"{API}/analyst/ask", json={"symbol": "DEMO_001"}, headers=admin_headers).json()
    assert r["mode"] == "rule_based" and "not configured" in r["note"]
    assert app_client.post(f"{API}/analyst/ask", json={"question": "hi there"}, headers=admin_headers).status_code == 422
    assert app_client.post(f"{API}/analyst/ask", json={"symbol": "NOPE"}, headers=admin_headers).status_code == 404


def test_analyst_permission(app_client):
    from tests.conftest import make_user

    std = make_user(app_client, "p5std@example.com")
    assert app_client.post(f"{API}/analyst/ask", json={"symbol": "DEMO_001"}, headers=std).status_code == 403



def test_rule_based_answer_follows_the_question():
    from app.services.analyst_service import question_intent, rule_based_answer

    s = {"symbol": "X", "strategy": {"name": "Breakout"}, "direction": "LONG", "status": "NO_TRADE", "stop": 95.0, "stop_method": "2×ATR",
         "checks": [{"name": "Backtest validity", "passed": False, "severity": "block", "detail": "expectancy -0.1R"},
                    {"name": "News flow", "passed": False, "severity": "warn", "detail": "2 negative headlines"}],
         "explanation": {"invalidation": ["Close below 95"], "trigger": "t"},
         "historical_evidence": {"sample_size": 40, "backtest_period": ["2019-01-01", "2026-09-01"], "t1_hit_rate": 42.0, "t2_hit_rate": 20.0,
                                 "stop_rate": 45.0, "neither_rate": 13.0, "expectancy_r": -0.1, "avg_holding_bars": 9, "conditioning": "strategy", "t1_ci95": [27, 58]}}
    assert question_intent("What could invalidate it?") == "invalidation" and question_intent("How strong is the historical evidence?") == "evidence"
    inv = rule_based_answer({"setup": s}, "What could invalidate it?")
    assert "Close below 95" in inv and "Currently blocked by: Backtest validity" in inv and "T1 before stop" not in inv
    ev = rule_based_answer({"setup": s}, "How strong is the historical evidence?")
    assert "40 comparable historical trades" in ev and "not a forecast" in ev and "Close below 95" not in ev
    risks = rule_based_answer({"setup": s}, "What are the risks?")
    assert "2 negative headlines" in risks
    none = rule_based_answer({"active_setups": [], "active_setups_note": "No setup.", "recent_news": [
        {"title": "[SAMPLE] a", "source": "S", "published_at": "2026-09-29T00:00:00", "sentiment_label": "Negative"}]}, "Why?")
    assert "Recent news" in none and "1 negative" in none


def test_signal_history_and_track_record_deduplicate(app_client, admin_headers, p5):
    for _ in range(2):  # rescan the same session twice
        r = app_client.post(f"{API}/admin/jobs/scan?market=FX", headers=admin_headers)
        assert app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()["status"] == "done"
    from app.core.db import SessionLocal
    from sqlalchemy import func, select
    from app.models import Signal, SignalOutcome

    db = SessionLocal()
    try:
        rows = db.execute(select(Signal.symbol, Signal.strategy_key, Signal.as_of, func.count()).join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
                          .where(Signal.market == "FX").group_by(Signal.symbol, Signal.strategy_key, Signal.as_of)).all()
    finally:
        db.close()
    assert all(r[3] == 1 for r in rows)  # one tracked outcome per published setup
    hist = app_client.get(f"{API}/stocks/DEMO_GBPUSD/analysis", headers=admin_headers).json()["signal_history"]
    keys = [(h["as_of"], h["strategy"]) for h in hist]
    assert len(keys) == len(set(keys))
