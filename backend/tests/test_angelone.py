"""Angel One SmartAPI adapter (fully mocked: no network, no credentials)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import pandas as pd
import pytest

from app.providers import angelone as ao

MASTER = [
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "expiry": "", "strike": "-1.0", "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE", "tick_size": "10.0"},
    {"token": "9999", "symbol": "RELIANCE-BE", "name": "RELIANCE", "expiry": "", "strike": "-1.0", "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE", "tick_size": "5.0"},
    {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY", "expiry": "", "strike": "0", "lotsize": "1", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0"},
    {"token": "99926004", "symbol": "Nifty 500", "name": "NIFTY 500", "expiry": "", "strike": "0", "lotsize": "1", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0"},
]


class Recorder:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req: httpx.Request):
        import json

        body = json.loads(req.content) if req.content else None
        self.calls.append((req.url.path, body, dict(req.headers)))
        fn = self.routes[req.url.path]
        return fn(body, req) if callable(fn) else httpx.Response(200, json=fn)


def _session(routes, **kw):
    rec = Recorder(routes)
    s = ao.AngelOneSession("KEY", "C123", "1234", "JBSWY3DPEHPK3PXP", client=httpx.Client(transport=httpx.MockTransport(rec)), pause_s=0, **kw)
    return s, rec


LOGIN = {"status": True, "data": {"jwtToken": "jwt-1", "refreshToken": "r", "feedToken": "f"}}


def test_login_candles_chunked_and_forming_bar_dropped(monkeypatch):
    today = datetime.now(ao.IST).date()

    def candles(body, req):
        a = datetime.strptime(body["fromdate"][:10], "%Y-%m-%d").date()
        b = datetime.strptime(body["todate"][:10], "%Y-%m-%d").date()
        days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
        return httpx.Response(200, json={"status": True, "data": [[f"{d}T00:00:00+05:30", 10, 11, 9, 10.5, 1000] for d in days]})

    s, rec = _session({"/rest/auth/angelbroking/user/v1/loginByPassword": LOGIN,
                       "/rest/secure/angelbroking/historical/v1/getCandleData": candles})
    s._master = MASTER
    p = ao.AngelOneProvider(s, history_days=2500, chunk_days=1000)
    inst = {i.symbol: i for i in p.list_instruments()}
    assert set(inst) == {"RELIANCE", "NIFTY50"} and inst["NIFTY50"].is_index and inst["RELIANCE"].extra["token"] == "2885"
    df, meta = p.get_ohlcv("RELIANCE")
    cand = [c for c in rec.calls if c[0].endswith("getCandleData")]
    assert len(cand) == 3                                   # 2500 days in 1000-day windows
    assert cand[0][1]["interval"] == "ONE_DAY" and cand[0][1]["symboltoken"] == "2885"
    assert cand[0][2]["authorization"] == "Bearer jwt-1" and cand[0][2]["x-privatekey"] == "KEY"
    login = [c for c in rec.calls if c[0].endswith("loginByPassword")][0][1]
    assert login["clientcode"] == "C123" and login["password"] == "1234" and len(login["totp"]) == 6
    assert meta.source == "angelone" and not meta.is_sample and df.index.is_monotonic_increasing
    if datetime.now(ao.IST).hour < 16:
        assert df.index[-1].date() < today                  # still-forming candle not stored


def test_rate_limit_retried_and_expired_token_relogin(monkeypatch):
    monkeypatch.setattr(ao.time, "sleep", lambda s: None)
    state = {"n": 0, "logins": 0}

    def login(body, req):
        state["logins"] += 1
        return httpx.Response(200, json={"status": True, "data": {"jwtToken": f"jwt-{state['logins']}"}})

    def candles(body, req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(200, json={"status": False, "message": "Access denied because of exceeding access rate", "errorcode": "AB1004"})
        if state["n"] == 2:
            return httpx.Response(403, json={"status": False, "message": "Invalid Token", "errorcode": "AG8001"})
        return httpx.Response(200, json={"status": True, "data": [["2024-01-02T00:00:00+05:30", 1, 2, 0.5, 1.5, 10]]})

    s, rec = _session({"/rest/auth/angelbroking/user/v1/loginByPassword": login,
                       "/rest/secure/angelbroking/historical/v1/getCandleData": candles})
    s._master = MASTER
    df, _ = ao.AngelOneProvider(s).get_ohlcv("RELIANCE", start=date(2024, 1, 1), end=date(2024, 1, 3))
    assert len(df) == 1 and state["logins"] == 2          # re-logged in after the expired token


def test_missing_credentials_and_errors_are_clear():
    s = ao.AngelOneSession(None, None, None, None, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(ao.AngelOneError, match="not configured"):
        s.login()
    assert ao.AngelOneProvider(s).health()["ok"] is False
    s2, _ = _session({"/rest/auth/angelbroking/user/v1/loginByPassword": {"status": False, "message": "Invalid totp", "errorcode": "AB1050"}})
    with pytest.raises(ao.AngelOneError, match="Invalid totp"):
        s2.login()


def test_option_chain_from_quotes():
    exp = (datetime.now(ao.IST).date() + timedelta(days=5)).strftime("%d%b%Y").upper()
    master = MASTER + [
        {"token": "101", "symbol": f"NIFTY{exp[:5]}{exp[-2:]}25000CE", "name": "NIFTY", "expiry": exp, "strike": "2500000.000000", "lotsize": "65", "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
        {"token": "102", "symbol": f"NIFTY{exp[:5]}{exp[-2:]}25000PE", "name": "NIFTY", "expiry": exp, "strike": "2500000.000000", "lotsize": "65", "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
        {"token": "103", "symbol": f"NIFTY{exp[:5]}{exp[-2:]}40000CE", "name": "NIFTY", "expiry": exp, "strike": "4000000.000000", "lotsize": "65", "instrumenttype": "OPTIDX", "exch_seg": "NFO"},
    ]

    def quote(body, req):
        ex, toks = next(iter(body["exchangeTokens"].items()))
        if ex == "NSE":
            return httpx.Response(200, json={"status": True, "data": {"fetched": [{"symbolToken": "99926000", "ltp": 25010.0}]}})
        fetched = [{"symbolToken": "101", "ltp": 120.0, "tradeVolume": 5000, "opnInterest": 90000,
                    "depth": {"buy": [{"price": 119.5, "quantity": 65}], "sell": [{"price": 120.5, "quantity": 65}]}},
                   {"symbolToken": "102", "ltp": 110.0, "tradeVolume": 0, "opnInterest": 1000, "depth": {"buy": [{"price": 0}], "sell": []}}]
        assert "103" not in toks                              # far OTM strikes outside ±10% are not requested
        return httpx.Response(200, json={"status": True, "data": {"fetched": [f for f in fetched if f["symbolToken"] in toks]}})

    s, _ = _session({"/rest/auth/angelbroking/user/v1/loginByPassword": LOGIN, "/rest/secure/angelbroking/market/v1/quote": quote})
    s._master = master
    snap = ao.AngelOneOptionsProvider(s).get_option_chain("NIFTY50")
    c = snap.chain.set_index("option_type")
    assert snap.spot == 25010.0 and snap.lot_size == 65 and len(c) == 2
    assert c.loc["CE", "strike"] == 25000.0 and c.loc["CE", "bid"] == 119.5 and c.loc["CE", "ask"] == 120.5 and c.loc["CE", "oi"] == 90000
    assert pd.isna(c.loc["PE", "bid"]) and pd.isna(c.loc["PE", "ask"])  # no real quotes: never invented
    assert snap.as_of.tzinfo is not None and not snap.meta.is_sample


def test_oi_change_derived_from_previous_snapshot(app_client, scanned, monkeypatch):

    from app.core.db import SessionLocal
    from app.models import OptionChainRow, OptionContract
    from app.providers.base import ChainSnapshot, DataMeta
    from app.services import options_service

    exp = date.today() + timedelta(days=9)
    state = {"oi": 1000.0, "t": datetime(2026, 1, 5, 15, 30, tzinfo=ao.IST)}

    class OiOnly:
        name, is_sample = "oi-only", False

        def get_option_chain(self, underlying, as_of=None):
            df = pd.DataFrame([{"expiry": exp, "strike": 777.0, "option_type": "CE", "bid": 1.0, "ask": 1.1, "ltp": 1.05,
                                "volume": 10.0, "oi": state["oi"], "oi_change": float("nan")}])
            return ChainSnapshot(underlying, state["t"], 800.0, 50, df, DataMeta("oi-only", False, str(exp), "now"))

        def get_iv_history(self, underlying):
            return None

    monkeypatch.setattr(options_service, "options_provider", lambda: OiOnly())
    db = SessionLocal()
    try:
        options_service.ingest_chain(db)
        state.update(oi=1600.0, t=state["t"] + timedelta(days=1))
        options_service.ingest_chain(db)
        rows = (db.query(OptionChainRow.snapshot_ts, OptionChainRow.oi, OptionChainRow.oi_change).join(OptionContract, OptionContract.id == OptionChainRow.contract_id)
                .filter(OptionContract.strike == 777.0, OptionContract.expiry == exp).order_by(OptionChainRow.snapshot_ts).all())
        assert [r.oi_change for r in rows] == [0.0, 600.0]      # first snapshot has no previous → 0; then the real difference
    finally:
        db.close()
