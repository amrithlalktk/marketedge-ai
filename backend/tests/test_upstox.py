"""Upstox adapter + daily connect flow (fully mocked: no network, no real credentials)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pandas as pd
import pytest

from app.providers import upstox as ux

MASTER = [
    {"segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|INE002A01018", "trading_symbol": "RELIANCE", "name": "RELIANCE INDUSTRIES", "isin": "INE002A01018", "tick_size": 10.0, "lot_size": 1},
    {"segment": "NSE_EQ", "instrument_type": "BE", "instrument_key": "NSE_EQ|INE999X01011", "trading_symbol": "JUNK", "name": "JUNK", "tick_size": 5.0},
    {"segment": "NSE_INDEX", "instrument_type": "INDEX", "instrument_key": "NSE_INDEX|Nifty 50", "trading_symbol": "NIFTY", "name": "Nifty 50"},
    {"segment": "NSE_INDEX", "instrument_type": "INDEX", "instrument_key": "NSE_INDEX|India VIX", "trading_symbol": "INDIA VIX", "name": "India VIX"},
]


def test_token_expires_0330_ist_next_day():
    assert ux.next_expiry_ist(datetime(2026, 1, 5, 20, 0, tzinfo=ux.IST)) == datetime(2026, 1, 6, 3, 30, tzinfo=ux.IST)
    assert ux.next_expiry_ist(datetime(2026, 1, 6, 2, 0, tzinfo=ux.IST)) == datetime(2026, 1, 6, 3, 30, tzinfo=ux.IST)


def test_authorize_url_and_code_exchange():
    q = parse_qs(urlparse(ux.authorize_url("CID", "http://localhost:3000/api/v1/upstox/callback", "st8")).query)
    assert q == {"client_id": ["CID"], "redirect_uri": ["http://localhost:3000/api/v1/upstox/callback"], "response_type": ["code"], "state": ["st8"]}
    seen = {}

    def h(req):
        seen.update(parse_qs(req.content.decode()))
        return httpx.Response(200, json={"access_token": "AT", "extended_token": None, "user_id": "U1"})

    tok = ux.exchange_code("C0DE", "CID", "SECRET", "http://x/cb", http=httpx.Client(transport=httpx.MockTransport(h)))
    assert tok["access_token"] == "AT" and seen["grant_type"] == ["authorization_code"] and seen["client_secret"] == ["SECRET"]
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"status": "error", "errors": [{"message": "Invalid Auth code"}]})))
    with pytest.raises(ux.UpstoxError, match="Invalid Auth code"):
        ux.exchange_code("x", "CID", "S", "http://x/cb", http=bad)


def _client(handler, token=None):
    tok = token if token is not None else {"access_token": "AT", "expires_at": (datetime.now(ux.IST) + timedelta(hours=5)).isoformat()}
    c = ux.UpstoxClient(lambda: tok, client=httpx.Client(transport=httpx.MockTransport(handler)), pause_s=0)
    c._master = MASTER
    return c


def test_candles_sorted_chunked_and_auth(monkeypatch):
    monkeypatch.setattr(ux.time, "sleep", lambda s: None)
    calls = []

    def h(req):
        calls.append(req)
        parts = req.url.raw_path.decode().split("/")  # /v3/historical-candle/{key}/days/1/{to}/{from}
        to, fr = date.fromisoformat(parts[-2]), date.fromisoformat(parts[-1])
        days = [to - timedelta(days=i) for i in range((to - fr).days + 1)]  # newest first, like Upstox
        return httpx.Response(200, json={"status": "success", "data": {"candles": [[f"{d}T00:00:00+05:30", 10, 11, 9, 10.5, 1000, 0] for d in days]}})

    p = ux.UpstoxProvider(_client(h), history_days=4000)
    inst = {i.symbol: i for i in p.list_instruments()}
    assert set(inst) == {"RELIANCE", "NIFTY50", "INDIAVIX"} and inst["NIFTY50"].is_index
    df, meta = p.get_ohlcv("RELIANCE")
    assert len(calls) == 2                                           # 4000 days → two ≤10-year windows
    assert "NSE_EQ%7CINE002A01018" in calls[0].url.raw_path.decode()  # instrument key URL-encoded
    assert calls[0].headers["authorization"] == "Bearer AT"
    assert df.index.is_monotonic_increasing and not df.index.has_duplicates and meta.source == "upstox" and not meta.is_sample
    if datetime.now(ux.IST).hour < 16:
        assert df.index[-1].date() < datetime.now(ux.IST).date()    # forming candle dropped

    expired = {"access_token": "AT", "expires_at": (datetime.now(ux.IST) - timedelta(minutes=1)).isoformat()}
    with pytest.raises(ux.UpstoxAuthError, match="Connect Upstox"):
        ux.UpstoxProvider(_client(h, token=expired)).preflight()
    with pytest.raises(ux.UpstoxAuthError):
        ux.UpstoxProvider(_client(lambda r: httpx.Response(401, json={"status": "error"}))).get_ohlcv("RELIANCE")
    n = {"i": 0}

    def limited(req):
        n["i"] += 1
        return httpx.Response(429, json={}) if n["i"] == 1 else h(req)

    assert len(ux.UpstoxProvider(_client(limited)).get_ohlcv("RELIANCE", start=date(2024, 1, 1), end=date(2024, 1, 3))[0]) == 3


def test_option_chain_parse():
    exp = datetime.now(ux.IST).date() + timedelta(days=6)
    ms = int(datetime(exp.year, exp.month, exp.day, 15, 30, tzinfo=ux.IST).timestamp() * 1000)
    master = MASTER + [{"segment": "NSE_FO", "instrument_type": t, "underlying_key": "NSE_INDEX|Nifty 50", "expiry": ms, "lot_size": 65,
                        "strike_price": k} for k in (25000.0, 40000.0) for t in ("CE", "PE")]

    def h(req):
        assert req.url.params["expiry_date"] == f"{exp:%Y-%m-%d}"
        md = lambda bid, ask, oi, prev: {"ltp": 100.0, "volume": 5000, "oi": oi, "prev_oi": prev, "bid_price": bid, "ask_price": ask}  # noqa: E731
        return httpx.Response(200, json={"status": "success", "data": [
            {"expiry": f"{exp}", "strike_price": 25000.0, "underlying_spot_price": 25010.0,
             "call_options": {"market_data": md(99.5, 100.5, 90000, 80000), "option_greeks": {"iv": 12.5, "delta": 0.5}},
             "put_options": {"market_data": md(0, 0, 1000, None), "option_greeks": {"iv": 0}}},
            {"expiry": f"{exp}", "strike_price": 40000.0, "underlying_spot_price": 25010.0,  # far OTM: outside ±10%
             "call_options": {"market_data": md(1, 2, 5, 5), "option_greeks": {}}, "put_options": {}}]})

    c = _client(h)
    c._master = master
    snap = ux.UpstoxOptionsProvider(c, n_expiries=1).get_option_chain("NIFTY50")
    ch = snap.chain.set_index("option_type")
    assert snap.spot == 25010.0 and snap.lot_size == 65 and len(ch) == 2 and snap.as_of.tzinfo is not None
    assert ch.loc["CE", "oi_change"] == 10000 and ch.loc["CE", "iv"] == pytest.approx(0.125) and ch.loc["CE", "bid"] == 99.5
    assert pd.isna(ch.loc["PE", "bid"]) and pd.isna(ch.loc["PE", "oi_change"]) and pd.isna(ch.loc["PE", "iv"])  # 0 quote ≠ a price


def test_connect_callback_flow(app_client, admin_headers, monkeypatch):
    from app.api.routes import upstox as route
    from app.providers.registry import provider_secret

    assert app_client.post("/api/v1/upstox/connect", headers=admin_headers).status_code == 409  # keys not stored yet
    for n, v in (("API_KEY", "CID"), ("API_SECRET", "SECRET")):
        assert app_client.post("/api/v1/admin/providers/keys", headers=admin_headers, json={"provider": "upstox", "name": n, "value": v}).status_code == 201
    url = app_client.post("/api/v1/upstox/connect", headers=admin_headers).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    assert parse_qs(urlparse(url).query)["client_id"] == ["CID"]
    got = {}

    def fake_exchange(code, cid, secret, redirect):
        got.update(code=code, cid=cid, secret=secret)
        return {"access_token": "TOKEN-XYZ", "expires_at": ux.next_expiry_ist().isoformat()}

    monkeypatch.setattr(route, "exchange_code", fake_exchange)
    r = app_client.get(f"/api/v1/upstox/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("upstox=connected")
    assert got == {"code": "abc", "cid": "CID", "secret": "SECRET"} and provider_secret("upstox", "ACCESS_TOKEN") == "TOKEN-XYZ"
    replay = app_client.get(f"/api/v1/upstox/callback?code=abc&state={state}", follow_redirects=False)
    assert "error" in replay.headers["location"]                                  # state is single-use (CSRF)
    assert "error" in app_client.get("/api/v1/upstox/callback?code=abc&state=forged", follow_redirects=False).headers["location"]
    st = app_client.get("/api/v1/upstox/status", headers=admin_headers).json()
    assert st["configured"] and st["connected"] and "TOKEN" not in json.dumps(st)   # the token itself is never returned
    keys = app_client.get("/api/v1/admin/providers", headers=admin_headers).json()["api_keys"]
    assert "TOKEN-XYZ" not in json.dumps(keys)


def test_ingest_stops_once_when_not_connected(app_client):
    from app.core.db import SessionLocal
    from app.services.market_data import ingest

    expired = {"access_token": None, "expires_at": None}
    calls = []
    p = ux.UpstoxProvider(ux.UpstoxClient(lambda: expired, client=httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500)))))
    db = SessionLocal()
    try:
        with pytest.raises(ux.UpstoxAuthError):
            ingest(db, p, "NSE")
        assert calls == []  # not even the master download: one clear failure
    finally:
        db.close()


def test_reminder_notifies_admins_when_not_connected(app_client, monkeypatch):
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.models import Notification
    from app.providers import registry
    from app.workers.tasks import upstox_reminder_db

    monkeypatch.setattr(get_settings(), "market_data_provider", "upstox")
    monkeypatch.setattr(registry, "upstox_token", lambda: {"access_token": None, "expires_at": None})
    db = SessionLocal()
    try:
        before = db.query(Notification).count()
        out = upstox_reminder_db(db)
        assert out["connected"] is False and out["notified"] >= 1
        assert db.query(Notification).count() == before + out["notified"]
    finally:
        db.close()


def test_analytics_token_verified_stored_and_preferred(app_client, admin_headers, monkeypatch):
    from app.api.routes import upstox as route
    from app.providers import registry

    def reject(tok):
        raise ux.UpstoxAuthError("Upstox did not accept this token")

    monkeypatch.setattr(route, "verify_token", reject)
    r = app_client.put("/api/v1/upstox/analytics-token", headers=admin_headers, json={"token": "x" * 40})
    assert r.status_code == 422 and registry.provider_secret("upstox", "ANALYTICS_TOKEN") is None  # never stored unverified
    monkeypatch.setattr(route, "verify_token", lambda tok: None)
    r = app_client.put("/api/v1/upstox/analytics-token", headers=admin_headers, json={"token": "  ANALYTICS-" + "y" * 40 + "  "})
    assert r.status_code == 200
    registry._UPSTOX_MEMO.update(at=None)
    t = registry.upstox_token()
    assert t["kind"] == "analytics" and t["access_token"] == "ANALYTICS-" + "y" * 40      # trimmed, preferred over the daily token
    assert datetime.fromisoformat(t["expires_at"]) > datetime.now(ux.IST) + timedelta(days=360)
    st = app_client.get("/api/v1/upstox/status", headers=admin_headers).json()
    assert st["mode"] == "analytics" and st["connected"] and "ANALYTICS-" not in json.dumps(st)
    ux.UpstoxClient(registry.upstox_token).preflight()  # usable without any daily login


def test_today_bar_from_batch_quotes(monkeypatch):
    fixed = datetime.now(ux.IST).replace(hour=18, minute=30, second=0, microsecond=0)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(ux, "datetime", FakeDT)
    today, yday = fixed.date(), fixed.date() - timedelta(days=1)
    day_ms = lambda d: int(datetime(d.year, d.month, d.day, tzinfo=ux.IST).timestamp() * 1000)  # noqa: E731
    batches = []

    def h(req):
        if req.url.path.endswith("/market-quote/ohlc"):
            keys = req.url.params["instrument_key"].split(",")
            batches.append(keys)
            return httpx.Response(200, json={"status": "success", "data": {
                "NSE_EQ:RELIANCE": {"instrument_token": "NSE_EQ|INE002A01018", "live_ohlc": {"open": 100, "high": 110, "low": 95, "close": 105, "volume": 9, "ts": day_ms(today)}},
                "NSE_INDEX:Nifty 50": {"instrument_token": "NSE_INDEX|Nifty 50", "live_ohlc": {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 0, "ts": day_ms(yday)}}}})
        return httpx.Response(200, json={"status": "success", "data": {"candles": [[f"{yday}T00:00:00+05:30", 90, 99, 89, 98, 5, 0]]}})

    p = ux.UpstoxProvider(_client(h))
    assert p.prefetch(["RELIANCE", "NIFTY50", "INDIAVIX"]) == 1        # Nifty quote is dated yesterday (holiday case) → skipped
    assert len(batches) == 1 and len(batches[0]) == 3
    df, _ = p.get_ohlcv("RELIANCE", start=yday)
    assert list(df.index.date) == [yday, today] and df.loc[pd.Timestamp(today), "close"] == 105
    assert list(p.get_ohlcv("NIFTY50", start=yday)[0].index.date) == [yday]


def test_rejected_token_is_surfaced(app_client, admin_headers, monkeypatch):
    """Upstox's historical API is public, so a dead token only shows up on quotes/option chain: it must be visible."""
    from app.api.routes import upstox as route
    from app.providers import registry

    monkeypatch.setattr(route, "verify_token", lambda tok: None)
    assert app_client.put("/api/v1/upstox/analytics-token", headers=admin_headers, json={"token": "LIVE-" + "z" * 40}).status_code == 200
    registry._UPSTOX_MEMO.update(at=None)
    assert app_client.get("/api/v1/upstox/status", headers=admin_headers).json()["connected"] is True
    c = ux.UpstoxClient(registry.upstox_token, client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"status": "error", "errors": [{"errorCode": "UDAPI100050", "message": "Invalid token used to access API"}]}))), pause_s=0)
    with pytest.raises(ux.UpstoxAuthError):
        c.get("/v2/option/chain", {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": "2026-10-06"})
    st = app_client.get("/api/v1/upstox/status", headers=admin_headers).json()
    assert st["connected"] is False and st["rejected_at"]
    # a new token clears it (different fingerprint)
    assert app_client.put("/api/v1/upstox/analytics-token", headers=admin_headers, json={"token": "NEW-" + "q" * 40}).status_code == 200
    registry._UPSTOX_MEMO.update(at=None)
    assert app_client.get("/api/v1/upstox/status", headers=admin_headers).json()["connected"] is True
