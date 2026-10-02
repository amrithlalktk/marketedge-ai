"""Phase 7: paper execution (parity with the backtester), alert conditions, journal analytics,
notification channels (all mocked — nothing is really sent), Telegram linking, API flows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import numpy as np
import pandas as pd
import pytest

from engine.paper import PaperOrder, close_at_market, mark_to_market, step

API = "/api/v1"


def bars(rows, start="2026-01-05"):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.bdate_range(start, periods=len(rows))).assign(volume=1e6)


NOCOST = dict(commission_pct=0.0, slippage_pct=0.0)
T0 = pd.Timestamp("2026-01-02")  # order placed on the bar before the first test bar


# ------------------------------------------------------------------ paper engine
def test_market_order_fills_next_open_never_the_order_bar():
    b = bars([[100, 101, 99, 100], [101, 102, 100, 101]])
    o = PaperOrder("LONG", 10, "market", created_at=b.index[0], **NOCOST)
    step(o, b)
    assert o.filled_at == b.index[1] and o.filled_price == 101  # bar 0 is the order bar


def test_limit_and_stop_entries():
    b = bars([[100, 101, 99, 100], [100, 100.5, 97.5, 98], [96, 97, 95, 96]])
    lim = PaperOrder("LONG", 1, "limit", created_at=T0, limit_price=98.0, **NOCOST)
    step(lim, b)
    assert lim.filled_price == 98.0 and lim.filled_at == b.index[1]
    gap = PaperOrder("LONG", 1, "limit", created_at=b.index[1], limit_price=97.0, **NOCOST)
    step(gap, b)
    assert gap.filled_price == 96.0  # gapped below the limit → filled at the (better) open
    stp = PaperOrder("SHORT", 1, "stop", created_at=T0, limit_price=99.0, **NOCOST)
    step(stp, b)
    assert stp.filled_price == 99.0 and stp.status == "open"


def test_gap_through_stop_same_bar_priority_partial_and_breakeven():
    b = bars([[100, 101, 99.5, 100.5], [100.5, 106, 99.8, 105], [104, 104.5, 99.9, 100], [99, 99.5, 98, 99]])
    o = PaperOrder("LONG", 100, "market", created_at=T0, stop=97.0, target1=105.0, target2=110.0, partial_at_t1=0.5, **NOCOST)
    step(o, b)
    kinds = [f["kind"] for f in o.fills]
    assert kinds[:2] == ["entry", "target1_partial"] and o.stop == 100.0  # breakeven after T1
    assert kinds[-1] == "breakeven" and o.status == "closed"
    assert o.realized == pytest.approx(50 * 5 + 50 * 0)
    both = PaperOrder("LONG", 1, "market", created_at=T0, stop=99.0, target2=101.0, **NOCOST)
    step(both, bars([[100, 100, 100, 100], [100, 102, 98, 100]]))
    assert both.exit_reason == "stop"  # bar touching stop and target → stop first
    gapper = PaperOrder("LONG", 1, "market", created_at=T0, stop=95.0, **NOCOST)
    step(gapper, bars([[100, 101, 99, 100], [90, 91, 89, 90]]))
    assert gapper.exit_reason == "stop_gap" and gapper.fills[-1]["price"] == 90


def test_expiry_time_exit_manual_close_costs_and_idempotency():
    b = bars([[100, 101, 99, 100]] * 6)
    exp = PaperOrder("LONG", 1, "limit", created_at=T0, limit_price=50.0, expires_at=b.index[2], **NOCOST)
    step(exp, b)
    assert exp.status == "cancelled" and exp.exit_reason == "expired"
    t = PaperOrder("LONG", 1, "market", created_at=T0, max_hold_bars=3, **NOCOST)
    step(t, b)
    assert t.exit_reason == "time" and t.bars_held == 3
    m = PaperOrder("SHORT", 2, "market", created_at=T0, commission_pct=0.1, slippage_pct=0.05)
    step(m, b.iloc[:2])
    n_fills = len(m.fills)
    step(m, b.iloc[:2])
    assert len(m.fills) == n_fills  # re-processing the same bars adds nothing
    assert mark_to_market(m, 99.0)["unrealized"] > 0
    close_at_market(m, b, b.index[2])
    assert m.exit_reason == "manual" and m.closed_at == b.index[3] and m.costs > 0


def test_paper_outcomes_match_backtester():
    """Same bars, same levels → paper and backtest agree on the outcome (target/stop/time)."""
    from engine.backtest import simulate_trade
    from engine.config import BacktestConfig, CostModel

    rng = np.random.default_rng(7)
    agree = 0
    for _ in range(40):
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, 30)))
        o_ = np.concatenate([[100], close[:-1]])
        hi, lo = np.maximum(o_, close) * 1.006, np.minimum(o_, close) * 0.994
        df = pd.DataFrame({"open": o_, "high": hi, "low": lo, "close": close, "volume": 1e6}, index=pd.bdate_range("2025-01-01", periods=30))
        df["atr"] = 2.0
        stop, t1, t2 = 100 * 0.96, 100 * 1.04, 100 * 1.08
        bt = simulate_trade(df, 0, "LONG", stop, t1, t2, BacktestConfig(max_hold_bars=20, partial_at_t1=0.5, costs=CostModel(0, 0)), max_chase_atr=100)
        po = PaperOrder("LONG", 1, "market", created_at=df.index[0], stop=stop, target1=t1, target2=t2, partial_at_t1=0.5, max_hold_bars=20, **NOCOST)
        step(po, df)
        if bt is None:
            continue
        agree += int(bt.exit_reason == po.exit_reason and bt.t1_hit == po.t1_done)
    assert agree >= 38  # identical rules; rare mismatches only from the backtester's data-end handling


# ------------------------------------------------------------------ alerts engine
def _feat(close, vol=None):
    from engine.features import build_features

    n = len(close)
    df = pd.DataFrame({"open": close, "high": np.asarray(close) * 1.01, "low": np.asarray(close) * 0.99, "close": close,
                       "volume": vol if vol is not None else np.full(n, 1e6)}, index=pd.bdate_range("2024-01-01", periods=n))
    return build_features(df)


def test_alert_conditions():
    from engine.alerts import evaluate, validate_params

    up = _feat(np.concatenate([np.full(80, 100.0), [99.0, 102.0]]))
    assert evaluate("price_above", {"level": 101.0}, up)[0] and not evaluate("price_below", {"level": 101.0}, up)[0]
    assert evaluate("price_cross", {"level": 100.5}, up)[0]
    assert evaluate("target_reached", {"level": 102.5, "direction": "LONG"}, up)[0]  # high 102*1.01
    assert evaluate("breakout", {}, up)[0]
    spike = _feat(np.full(80, 100.0), vol=np.r_[np.full(79, 1e6), 5e6])
    ok, msg, v = evaluate("volume_spike", {"multiple": 3.0}, spike)
    assert ok and "5.0×" in msg
    with pytest.raises(ValueError):
        validate_params("ema_cross_up", {"fast": 50, "slow": 20})
    with pytest.raises(ValueError):
        validate_params("price_above", {})


def test_unusual_options_detector():
    from engine.alerts import unusual_options

    rows = [{"strike": 25000 + 50 * k, "CE": {"oi_change": 100}, "PE": {"oi_change": 120}} for k in range(20)]
    rows[5]["CE"]["oi_change"] = 5000
    hits = unusual_options(rows, 3.0)
    assert hits[0]["strike"] == 25250 and hits[0]["option_type"] == "CE" and len(hits) == 1


def test_journal_analytics():
    from engine.journal import analytics

    closed = [{"pnl": 1000, "entry_at": "2026-01-02", "exit_at": "2026-01-09"}, {"pnl": -500, "entry_at": "2026-01-05", "exit_at": "2026-01-12"},
              {"pnl": 1500, "entry_at": "2026-01-10", "exit_at": "2026-01-20"}]
    a = analytics(closed, 100_000, [{"unrealized": 200, "exposure": 5000, "risk": 300, "market": "NSE", "sector": "IT"}])
    assert a["total_trades"] == 3 and a["win_rate"] == pytest.approx(66.7) and a["profit_factor"] == 5.0 and a["expectancy"] == pytest.approx(666.67)
    assert a["equity"] == 102_200 and a["exposure_by_sector"] == {"IT": 5000.0} and a["max_drawdown_pct"] < 0


# ------------------------------------------------------------------ channels (mocked)
def _n():
    return SimpleNamespace(title="T", body="B", link="/x")


def test_email_channel_sends_only_to_account_address(monkeypatch):
    from app.core.config import get_settings
    from app.notify.channels import EmailChannel

    s = get_settings()
    monkeypatch.setattr(s, "smtp_host", "smtp.test")
    monkeypatch.setattr(s, "smtp_from", "alerts@test")
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent["tls"] = True
        def login(self, u, p): sent["login"] = u
        def send_message(self, m): sent["to"], sent["subject"] = m["To"], m["Subject"]

    user = SimpleNamespace(email="me@example.com")
    assert EmailChannel().send(user, SimpleNamespace(email_enabled=False), _n(), smtp_factory=FakeSMTP)[0] == "skipped"
    assert EmailChannel().send(user, SimpleNamespace(email_enabled=True), _n(), smtp_factory=FakeSMTP) == ("sent", None)
    assert sent["to"] == "me@example.com" and sent["tls"] and "[MarketEdge]" in sent["subject"]


def test_telegram_push_whatsapp_channels(monkeypatch):
    from app.core.config import get_settings
    from app.notify import channels as ch

    s = get_settings()
    assert ch.TelegramChannel().send(None, SimpleNamespace(telegram_chat_id="1"), _n())[0] == "not_configured"
    monkeypatch.setattr(s, "telegram_bot_token", "TOK")
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), req.content
        return httpx.Response(200, json={"ok": True})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert ch.TelegramChannel().send(None, SimpleNamespace(telegram_chat_id="42"), _n(), http=http) == ("sent", None)
    assert "botTOK/sendMessage" in seen["url"] and b'"chat_id":"42"' in seen["body"].replace(b" ", b"")
    assert ch.TelegramChannel().send(None, SimpleNamespace(telegram_chat_id=None), _n(), http=http)[0] == "skipped"
    calls = []
    setting = SimpleNamespace(push_subscriptions=[{"endpoint": "https://p/1", "keys": {}}, {"endpoint": "https://p/2", "keys": {}}])

    def fake_webpush(subscription_info, **kw):
        calls.append(subscription_info["endpoint"])
        if subscription_info["endpoint"].endswith("2"):
            raise Exception("gone")
    assert ch.PushChannel().send(None, setting, _n(), webpush_fn=fake_webpush)[0] == "sent" and len(calls) == 2
    assert ch.WhatsAppChannel().send(None, SimpleNamespace(whatsapp_number="+911234567890"), _n())[0] == "not_configured"


def test_quiet_hours():
    from app.services.notify_service import _quiet

    s = SimpleNamespace(quiet_start_hour=22, quiet_end_hour=7)
    night = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)  # 23:30 IST
    day = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)     # 11:30 IST
    assert _quiet(s, night) and not _quiet(s, day)


# ------------------------------------------------------------------ API flows
def _append_bar(symbol: str, open_: float, high: float, low: float, close: float, days: int = 1):
    """Simulate the next session arriving for an instrument (tests only)."""
    from sqlalchemy import func, select

    from app.core.db import SessionLocal
    from app.models import DataStatus, Instrument, MarketBar

    db = SessionLocal()
    ins = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    last = db.scalar(select(func.max(MarketBar.ts)).where(MarketBar.instrument_id == ins.id, MarketBar.interval == "1d"))
    ts = last + timedelta(days=days)
    db.add(MarketBar(instrument_id=ins.id, interval="1d", ts=ts, open=open_, high=high, low=low, close=close, volume=2e6, source="test"))
    st = db.get(DataStatus, (ins.id, "1d"))
    st.last_bar_ts = ts
    db.commit()
    db.close()
    return ts


def _last_close(symbol):
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Instrument, MarketBar

    db = SessionLocal()
    ins = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    b = db.scalar(select(MarketBar).where(MarketBar.instrument_id == ins.id, MarketBar.interval == "1d").order_by(MarketBar.ts.desc()).limit(1))
    db.close()
    return float(b.close)


def test_paper_portfolio_flow(app_client, scanned):
    from tests.conftest import make_user

    h = make_user(app_client, "paper7@example.com")
    p = app_client.post(f"{API}/portfolios", json={"name": "Paper A"}, headers=h).json()
    assert app_client.post(f"{API}/portfolios", json={"name": "Paper B"}, headers=h).status_code == 403  # standard: one paper portfolio
    sym = "DEMO_010"
    c = _last_close(sym)
    r = app_client.post(f"{API}/portfolios/{p['id']}/orders", json={"symbol": sym, "direction": "LONG", "quantity": 10, "stop": round(c * 0.95, 2),
                                                                   "target1": round(c * 1.03, 2), "target2": round(c * 1.06, 2), "partial_at_t1": 0.5}, headers=h)
    assert r.status_code == 201 and r.json()["status"] == "pending"  # never fills on the bar it was placed on
    _append_bar(sym, c * 1.001, c * 1.035, c * 0.998, c * 1.03)  # next session: opens, reaches T1
    from app.core.db import SessionLocal
    from app.services.portfolio_service import process

    db = SessionLocal()
    process(db)
    db.close()
    v = app_client.get(f"{API}/portfolios/{p['id']}", headers=h).json()
    t = v["trades"][0]
    assert t["status"] == "open" and t["filled_price"] == pytest.approx(c * 1.001 * 1.0005, rel=1e-6)  # open + slippage
    assert [f["kind"] for f in t["fills"]] == ["entry", "target1_partial"] and t["stop"] == pytest.approx(t["filled_price"])
    assert t["open_quantity"] == 5 and v["analytics"]["open_positions"] == 1 and "backtest rules" in v["execution_note"]
    notes = app_client.get(f"{API}/notifications", headers=h).json()
    assert notes["unread"] >= 2 and any("filled" in n["title"] for n in notes["items"])
    assert app_client.post(f"{API}/portfolios/{p['id']}/trades/{t['id']}/close", headers=h).json()["status"] == "open"  # closes next open
    _append_bar(sym, c * 1.02, c * 1.025, c * 1.01, c * 1.02)
    db = SessionLocal()
    process(db)
    db.close()
    t = app_client.get(f"{API}/portfolios/{p['id']}", headers=h).json()["trades"][0]
    assert t["status"] == "closed" and t["exit_reason"] == "manual" and t["realized_base"] > 0
    other = make_user(app_client, "paper7b@example.com")
    assert app_client.get(f"{API}/portfolios/{p['id']}", headers=other).status_code == 404


def test_journal_portfolio(app_client, scanned):
    from tests.conftest import make_user

    h = make_user(app_client, "journal7@example.com")
    j = app_client.post(f"{API}/portfolios", json={"name": "My real trades", "kind": "journal", "starting_capital": 500000}, headers=h).json()
    r = app_client.post(f"{API}/portfolios/{j['id']}/journal", json={"symbol": "DEMO_011", "direction": "LONG", "quantity": 100, "entry_price": 100,
                                                                    "entry_at": "2026-05-01T10:00:00", "exit_price": 110, "exit_at": "2026-05-10T10:00:00",
                                                                    "brokerage": 40, "taxes": 60}, headers=h)
    assert r.status_code == 201
    a = app_client.get(f"{API}/portfolios/{j['id']}", headers=h).json()["analytics"]
    assert a["realized_pnl"] == 900.0 and a["total_trades"] == 1 and a["avg_holding_days"] == 9
    bad = app_client.post(f"{API}/portfolios/{j['id']}/journal", json={"symbol": "DEMO_011", "direction": "LONG", "quantity": 1, "entry_price": 100,
                                                                      "entry_at": "2026-05-10T10:00:00", "exit_price": 110, "exit_at": "2026-05-01T10:00:00"}, headers=h)
    assert bad.status_code == 422


def test_alerts_flow(app_client, admin_headers, scanned):
    from tests.conftest import make_user

    h = make_user(app_client, "alerts7@example.com")
    kinds = app_client.get(f"{API}/alerts/kinds", headers=h).json()
    assert {"price_above", "new_setup", "unusual_options"} <= {k["kind"] for k in kinds["kinds"]} and kinds["channels"]["web"]["configured"]
    sym = "DEMO_012"
    c = _last_close(sym)
    a = app_client.post(f"{API}/alerts", json={"kind": "price_above", "symbol": sym, "params": {"level": round(c * 1.02, 2)}, "note": "watch"}, headers=h)
    assert a.status_code == 201
    assert app_client.post(f"{API}/alerts", json={"kind": "price_above", "params": {"level": 1}}, headers=h).status_code == 422  # needs a symbol
    assert app_client.post(f"{API}/alerts", json={"kind": "rsi_cross_above", "symbol": sym, "params": {}}, headers=h).status_code == 422
    assert app_client.post(f"{API}/alerts", json={"kind": "price_above", "symbol": sym, "params": {"level": 1}, "channels": ["pigeon"]}, headers=h).status_code == 422
    app_client.post(f"{API}/alerts", json={"kind": "new_setup", "params": {"market": "NSE"}, "repeat": True}, headers=h)
    _append_bar(sym, c, c * 1.04, c * 0.99, c * 1.03)
    from app.core.db import SessionLocal
    from app.services.alert_service import evaluate_bar_alerts

    db = SessionLocal()
    first = evaluate_bar_alerts(db, "NSE")
    again = evaluate_bar_alerts(db, "NSE")
    db.close()
    assert first["fired"] >= 1 and again["fired"] == 0 and again["checked"] == 0  # idempotent per bar
    al = {x["kind"]: x for x in app_client.get(f"{API}/alerts", headers=h).json()["items"]}
    assert al["price_above"]["status"] == "triggered" and al["price_above"]["trigger_count"] == 1
    n = app_client.get(f"{API}/notifications?unread=true", headers=h).json()
    msg = next(x for x in n["items"] if x["payload"].get("alert_id") == al["price_above"]["id"])
    assert "above" in msg["body"] and "watch" in msg["body"] and msg["deliveries"]["web"]["status"] == "sent"
    app_client.post(f"{API}/notifications/read", headers=h)
    assert app_client.get(f"{API}/notifications", headers=h).json()["unread"] == 0
    for k in range(9):  # standard limit (10 active)
        app_client.post(f"{API}/alerts", json={"kind": "price_below", "symbol": sym, "params": {"level": 1 + k}}, headers=h)
    assert app_client.post(f"{API}/alerts", json={"kind": "price_below", "symbol": sym, "params": {"level": 50}}, headers=h).status_code == 403


def test_alerts_from_setup_and_new_setup_matching(app_client, admin_headers, scanned):
    from tests.conftest import any_signal_id

    sid = any_signal_id(app_client, admin_headers)
    r = app_client.post(f"{API}/alerts/from-setup/{sid}", json={"kinds": ["entry", "target1", "stop"]}, headers=admin_headers)
    assert r.status_code == 201 and {a["kind"] for a in r.json()["items"]} == {"entry_reached", "target_reached", "stop_reached"}
    from engine.alerts import matches_setup

    s = {"market": "NSE", "strategy": {"id": "breakout_volume"}, "direction": "LONG", "score": 72}
    assert matches_setup({"market": "NSE", "min_score": 70}, s) and not matches_setup({"direction": "SHORT"}, s)


def test_notification_settings_telegram_and_webhook(app_client, monkeypatch, scanned):
    from app.core.config import get_settings
    from tests.conftest import make_user

    h = make_user(app_client, "notif7@example.com")
    st = app_client.get(f"{API}/notifications/settings", headers=h).json()
    assert st["channels"]["email"]["configured"] is False and st["default_channels"] == ["web"]
    assert app_client.put(f"{API}/notifications/settings", json={"whatsapp_number": "9876"}, headers=h).status_code == 422
    assert app_client.put(f"{API}/notifications/settings", json={"whatsapp_number": "+919876543210"}, headers=h).status_code == 422  # opt-in
    ok = app_client.put(f"{API}/notifications/settings", json={"whatsapp_number": "+919876543210", "whatsapp_opt_in": True,
                                                                "quiet_start_hour": 22, "quiet_end_hour": 7}, headers=h).json()
    assert ok["whatsapp_number"] == "+919876543210" and ok["quiet_start_hour"] == 22
    link = app_client.post(f"{API}/notifications/telegram/link", headers=h).json()
    assert len(link["code"]) == 8
    upd = {"update_id": 1, "message": {"text": f"/start {link['code']}", "chat": {"id": 777}}}
    assert app_client.post(f"{API}/notifications/telegram/webhook", json=upd).status_code == 403  # no secret configured
    monkeypatch.setattr(get_settings(), "telegram_webhook_secret", "s3cret")
    assert app_client.post(f"{API}/notifications/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}).status_code == 403
    assert app_client.post(f"{API}/notifications/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"}).status_code == 200
    st = app_client.get(f"{API}/notifications/settings", headers=h).json()
    assert st["telegram_linked"] and "telegram" in st["default_channels"]
    again = app_client.post(f"{API}/notifications/telegram/webhook", json=upd, headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"})
    assert again.status_code == 200  # code is single-use: nothing re-linked
    sub = app_client.post(f"{API}/notifications/push/subscribe", json={"endpoint": "https://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}}, headers=h)
    assert sub.json()["subscriptions"] == 1
    t = app_client.post(f"{API}/notifications/test", headers=h).json()
    assert t["deliveries"]["web"]["status"] == "sent" and t["deliveries"]["telegram"]["status"] in ("not_configured", "skipped")



def test_trade_patch_validates_levels_and_kinds_list_optional_params(app_client, scanned):
    from tests.conftest import make_user

    h = make_user(app_client, "patch7@example.com")
    p = app_client.post(f"{API}/portfolios", json={"name": "P"}, headers=h).json()
    c = _last_close("DEMO_013")
    tid = app_client.post(f"{API}/portfolios/{p['id']}/orders", json={"symbol": "DEMO_013", "direction": "LONG", "quantity": 1, "order_type": "limit",
                                                                     "limit_price": round(c * 0.99, 2), "stop": round(c * 0.95, 2)}, headers=h).json()["trade_id"]
    assert app_client.patch(f"{API}/portfolios/{p['id']}/trades/{tid}", json={"stop": round(c * 1.05, 2)}, headers=h).status_code == 422
    assert app_client.patch(f"{API}/portfolios/{p['id']}/trades/{tid}", json={"target1": round(c * 1.1, 2), "target2": round(c * 1.05, 2)}, headers=h).status_code == 422
    assert app_client.patch(f"{API}/portfolios/{p['id']}/trades/{tid}", json={"stop": round(c * 0.96, 2)}, headers=h).status_code == 200
    k = {x["kind"]: x for x in app_client.get(f"{API}/alerts/kinds", headers=h).json()["kinds"]}
    assert k["new_setup"]["optional_params"] == ["market", "strategy", "direction", "min_score"]


def test_sample_data_is_prefix_stable():
    """A later 'today' only appends bars: incremental ingestion never rewrites synthetic history."""
    from datetime import date

    from app.providers import sample_crypto
    from app.providers.sample import SampleUniverse

    a, b = SampleUniverse(n_stocks=5, today=date(2026, 9, 29)), SampleUniverse(n_stocks=5, today=date(2026, 10, 2))
    for sym in ("DEMO_001", "DEMO_NIFTY50"):
        old, new = a.bars[sym], b.bars[sym]
        assert len(new) == len(old) + 3 and (new.loc[old.index] == old).all().all()
    x, y = sample_crypto.build("CRYPTO", date(2026, 9, 29)), sample_crypto.build("CRYPTO", date(2026, 10, 2))
    assert all((y.bars[k].loc[x.bars[k].index] == x.bars[k]).all().all() for k in x.bars)


def test_daily_ideas_digest_once_per_session(app_client, admin_headers, scanned):
    from app.core.db import SessionLocal
    from app.models import Notification
    from app.services.alert_service import daily_ideas_alerts, daily_ideas_text

    r = app_client.post(f"{API}/alerts", json={"kind": "daily_ideas", "params": {}, "repeat": True}, headers=admin_headers)
    assert r.status_code == 201, r.text
    db = SessionLocal()
    try:
        d = daily_ideas_text(db)
        assert d and d["as_of"]
        if d["ideas"]:
            assert "entry" in d["body"] and "stop loss" in d["body"] and "past cases" in d["body"] and "not guarantees" in d["body"]
        else:
            assert d["title"].startswith("No trade today") and "safety check" in d["body"]
        before = db.query(Notification).count()
        assert daily_ideas_alerts(db) >= 1
        assert daily_ideas_alerts(db) == 0                    # same session: never sent twice
        assert db.query(Notification).count() > before
    finally:
        db.close()
    kinds = {k["kind"]: k for k in app_client.get(f"{API}/alerts/kinds", headers=admin_headers).json()["kinds"]}
    assert kinds["daily_ideas"]["needs_symbol"] is False
