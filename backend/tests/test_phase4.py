"""Phase 4: market profiles, 24x7 calendar, price precision, volume-less scoring, crypto derivatives
context, FX conversion, provider adapters (mocked HTTP), sample-data invariants, multi-market API."""
from __future__ import annotations

from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest

from engine.crypto import derivatives_context
from engine.levels import price_decimals
from engine.markets import PROFILES, pips, profile
from engine.scoring import score_volume
from engine.validation import expected_last_session

API = "/api/v1"


# ------------------------------------------------------------------ engine
def test_calendars():
    wed, sat = date(2026, 9, 30), date(2026, 10, 3)
    assert expected_last_session(sat) == date(2026, 10, 2)  # weekday market → Friday
    assert expected_last_session(sat, calendar="24x7") == date(2026, 10, 2)  # yesterday's complete UTC day
    assert expected_last_session(wed, calendar="24x7") == date(2026, 9, 29)
    assert profile("CRYPTO").periods_per_year == 365 and profile("FX").volumeless and set(PROFILES) == {"NSE", "CRYPTO", "US", "EUROPE", "ASIA", "FX"}


def test_price_precision_matches_instrument_scale():
    assert price_decimals(25000) == 2 and price_decimals(1.0842) == 5 and price_decimals(0.00002) >= 9
    assert pips(1.2508 - 1.2215, "EURUSD", "USD") == 293.0 and pips(150.25 - 149.75, "USDJPY", "JPY") == 50.0


def test_volume_component_absent_for_volumeless():
    assert score_volume(pd.Series({"vol_sma20": 0.0, "vol_ratio20": np.nan}), 1) is None
    assert score_volume(pd.Series({"vol_sma20": 1e6, "vol_ratio20": 2.1, "obv_slope": 1, "mfi": 60}), 1) > 70


def _deriv(funding, oi_growth, ls, n=10):
    idx = pd.date_range("2026-09-01", periods=n, freq="D")
    return pd.DataFrame({"funding_rate": [funding] * n, "open_interest": 1e9 * np.cumprod([1 + oi_growth] * n), "long_short_ratio": [ls] * n}, index=idx)


def test_derivatives_context_warnings_and_interpretation():
    close = pd.Series(np.linspace(100, 110, 10), index=pd.date_range("2026-09-01", periods=10, freq="D"))
    hot = derivatives_context(close, _deriv(0.001, 0.02, 3.0), "LONG")
    assert hot["funding"]["state"] == "crowded longs" and "new longs" in hot["open_interest"]["interpretation"]
    assert {c["name"] for c in hot["checks"]} == {"Funding crowding", "Positioning"} and all(c["severity"] == "warn" for c in hot["checks"])
    cover = derivatives_context(close, _deriv(0.0001, -0.02, 1.1), "LONG")
    assert "short covering" in cover["open_interest"]["interpretation"] and cover["checks"][0]["name"] == "Open interest"
    assert derivatives_context(close, None)["available"] is False
    assert "not used in the score" in hot["note"]


def test_fx_book_direct_inverse_and_bridge():
    from app.services.fx_service import FxBook

    book = FxBook({("USD", "INR"): {"rate": 83.0, "symbol": "USDINR", "as_of": "2026-09-30", "is_sample": False},
                   ("EUR", "USD"): {"rate": 1.1, "symbol": "EURUSD", "as_of": "2026-09-29", "is_sample": False},
                   ("USD", "JPY"): {"rate": 150.0, "symbol": "USDJPY", "as_of": "2026-09-30", "is_sample": True}})
    assert book.convert("USD", "INR")["rate"] == 83.0
    assert book.convert("INR", "USD")["rate"] == pytest.approx(1 / 83)
    eur = book.convert("EUR", "INR")
    assert eur["rate"] == pytest.approx(91.3) and eur["path"] == ["EURUSD", "USDINR"] and eur["as_of"] == "2026-09-29"
    jpy = book.convert("JPY", "INR")
    assert jpy["rate"] == pytest.approx(83 / 150) and jpy["is_sample"] is True
    assert book.convert("USDT", "INR")["rate"] == 83.0 and book.convert("CHF", "INR") is None


# ------------------------------------------------------------------ sample data invariants
def test_sample_markets_are_consistent():
    from app.providers.sample_global import build

    fx = build("FX", date(2026, 9, 30)).bars
    a = fx["DEMO_EURINR"]["close"]
    b = fx["DEMO_EURUSD"]["close"] * fx["DEMO_USDINR"]["close"]
    assert np.allclose(a, b, rtol=1e-4)
    crypto = build("CRYPTO", date(2026, 9, 30))
    idx = crypto.bars["DEMO_BTC"].index
    assert idx[-1] == pd.Timestamp("2026-09-29") and (idx.weekday >= 5).any()  # 24x7, last complete day
    assert all(i.is_sample and i.symbol.startswith("DEMO_") for i in crypto.instruments)
    assert "DEMO_ILLIQ" not in crypto.derivatives


# ------------------------------------------------------------------ provider adapters (no network)
def _mock(routes):
    def handler(req: httpx.Request):
        for path, body in routes.items():
            if req.url.path == path:
                return httpx.Response(200, json=body(req) if callable(body) else body)
        return httpx.Response(404, json={"msg": "not found"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_binance_adapter_parsing():
    from app.providers.binance import BinanceProvider

    now_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
    day = 86_400_000
    midnight = now_ms - now_ms % day  # funding settlements within one UTC day are averaged
    opens = [now_ms - 2 * day - 3600e3, now_ms - day - 3600e3, now_ms - 3600e3]  # the last day is still forming
    kl = [[int(o), "1", "2", "0.5", "1.5", "100", int(o + day - 1), "150", 10, "0", "0", "0"] for o in opens]
    routes = {
        "/api/v3/exchangeInfo": {"symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "baseAsset": "BTC"},
            {"symbol": "USDCUSDT", "status": "TRADING", "quoteAsset": "USDT", "baseAsset": "USDC"},
            {"symbol": "ETHUPUSDT", "status": "TRADING", "quoteAsset": "USDT", "baseAsset": "ETHUP"},
            {"symbol": "OLDUSDT", "status": "BREAK", "quoteAsset": "USDT", "baseAsset": "OLD"}]},
        "/api/v3/ticker/24hr": [{"symbol": s, "quoteVolume": "1000"} for s in ("BTCUSDT", "USDCUSDT", "ETHUPUSDT", "OLDUSDT")],
        "/api/v3/ticker/bookTicker": [{"symbol": "BTCUSDT", "bidPrice": "99.99", "askPrice": "100.01"}],
        "/api/v3/klines": kl,
        "/fapi/v1/fundingRate": [{"fundingTime": midnight - day, "fundingRate": "0.0001"}, {"fundingTime": midnight - day + 8 * 3600_000, "fundingRate": "0.0003"}],
        "/futures/data/openInterestHist": [{"timestamp": now_ms - day, "sumOpenInterestValue": "5000000"}],
        "/futures/data/globalLongShortAccountRatio": [{"timestamp": now_ms - day, "longShortRatio": "1.5"}],
    }
    p = BinanceProvider(universe_size=5, client=_mock(routes), pause_s=0)
    inst = p.list_instruments()
    assert [i.symbol for i in inst] == ["BTCUSDT"] and inst[0].extra["spread_bps"] == pytest.approx(2.0, abs=0.01)
    df, meta = p.get_ohlcv("BTCUSDT")
    assert len(df) == 2 and meta.source == "binance" and not meta.is_sample  # still-forming kline dropped
    d = p.get_derivatives("BTCUSDT")
    assert d["funding_rate"].iloc[-1] == pytest.approx(0.0002) and d["long_short_ratio"].iloc[-1] == 1.5


def test_twelvedata_adapter(tmp_path):
    from app.providers.twelvedata import TwelveDataProvider

    (tmp_path / "universe").mkdir()
    (tmp_path / "universe" / "US.csv").write_text("symbol,vendor_symbol,name,asset_class,exchange,currency,sector,is_index\nAAPL,AAPL,Apple,EQUITY,NASDAQ,USD,Technology,0\n")
    today = pd.Timestamp.utcnow().date()
    vals = [{"datetime": str(today - pd.Timedelta(days=k)), "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "10"} for k in (2, 1, 0)]
    seen = {}

    def ts(req):
        seen.update(dict(req.url.params))
        return {"status": "ok", "values": vals}

    p = TwelveDataProvider("US", "KEY", str(tmp_path), rate_per_min=60000, client=_mock({"/time_series": ts}))
    assert p.list_instruments()[0].extra["vendor_symbol"] == "AAPL"
    df, meta = p.get_ohlcv("AAPL")
    assert len(df) == 2 and seen["exchange"] == "NASDAQ" and seen["apikey"] == "KEY"  # today's forming bar dropped
    err = TwelveDataProvider("US", "KEY", str(tmp_path), rate_per_min=60000, client=_mock({"/time_series": {"status": "error", "message": "bad symbol"}}))
    with pytest.raises(RuntimeError, match="bad symbol"):
        err.get_ohlcv("AAPL")
    with pytest.raises(RuntimeError, match="API key"):
        TwelveDataProvider("US", None, str(tmp_path)).get_ohlcv("AAPL")


# ------------------------------------------------------------------ API (sample data)
@pytest.fixture(scope="module")
def multi(app_client, admin_headers, scanned):
    for m in ("FX", "CRYPTO", "US"):
        for kind in ("ingest", "scan"):
            r = app_client.post(f"{API}/admin/jobs/{kind}?market={m}" + ("&full=true" if kind == "ingest" else ""), headers=admin_headers)
            assert r.status_code == 202
            job = app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()
            assert job["status"] == "done", job
    return True


def test_markets_listing_and_global_overview(app_client, admin_headers, multi):
    ms = {m["id"]: m for m in app_client.get(f"{API}/markets", headers=admin_headers).json()["items"]}
    assert ms["CRYPTO"]["calendar"] == "24x7" and ms["FX"]["last_scan"] and ms["NSE"]["last_scan"]
    g = app_client.get(f"{API}/markets/global-overview", headers=admin_headers).json()["markets"]
    assert {"NSE", "FX", "CRYPTO", "US"} <= set(g)
    assert g["CRYPTO"]["btc_dominance_pct"] and "tracked universe" in g["CRYPTO"]["btc_dominance_basis"]
    assert all(c["data"]["is_sample"] for c in g["FX"]["cards"]) and len(g["FX"]["cards"]) >= 14
    crypto_ov = app_client.get(f"{API}/markets/overview?market=CRYPTO", headers=admin_headers).json()
    assert crypto_ov["market"] == "CRYPTO" and crypto_ov["regime"]["regime"]


def test_market_signal_endpoints(app_client, admin_headers, multi):
    for path in ("/crypto/signals", "/forex/signals", "/global/signals", "/signals/top?market=FX"):
        body = app_client.get(API + path, headers=admin_headers).json()
        assert "disclaimer" in body and "sort_note" in body, path
        for s in body["items"]:
            assert s["probability"]["sample_size"] >= 30 and s["data"]["is_sample"]
            assert s["inr"]["rate"] > 0 and s["inr"]["via"] and s["inr"]["rate_as_of"]
            if s["market"] == "FX":
                assert s["pips"]["stop_pips"] > 0 and s["pips"]["rate_differential"]["available"] is False
            if s["market"] == "CRYPTO":
                assert "derivatives" in s
    nse = app_client.get(f"{API}/signals/top?limit=100", headers=admin_headers).json()
    assert all(i["market"] == "NSE" for i in nse["items"])  # markets never mix


def test_market_scoped_stocks_analysis_backtest(app_client, admin_headers, multi):
    fx = app_client.get(f"{API}/stocks?market=FX", headers=admin_headers).json()
    assert fx["total"] >= 14 and all(i["market"] == "FX" for i in fx["items"])
    a = app_client.get(f"{API}/stocks/DEMO_EURUSD/analysis?mode=technical", headers=admin_headers).json()
    assert a["data"]["is_sample"] and {x["id"][:3] for x in a["inactive_strategies"]} <= {"px_"}
    c = app_client.get(f"{API}/stocks/DEMO_ETH/analysis", headers=admin_headers).json()
    assert c["status"] in ("VALID", "NO_TRADE", "NO_SETUP")
    r = app_client.post(f"{API}/backtests", json={"market": "CRYPTO", "strategy_key": "breakout_volume", "walk_forward": False}, headers=admin_headers)
    bt = app_client.get(f"{API}/backtests/{r.json()['id']}", headers=admin_headers).json()
    assert bt["status"] == "done", bt["error"]
    assert bt["result"]["market"] == "CRYPTO" and bt["result"]["costs"]["commission_pct"] == 0.10 and bt["result"]["portfolio"]["config"]["periods_per_year"] == 365
    hr = app_client.get(f"{API}/analytics/hit-rates?market=FX&group_by=strategy_id", headers=admin_headers).json()
    assert hr["overall"]["sample_size"] > 0 and all(g["key"].startswith("px_") for g in hr["groups"])


def test_short_targets_never_below_zero():
    from engine.config import EngineConfig
    from engine.features import build_features
    from engine.levels import compute_levels
    from tests.test_engine_core import random_walk

    f = build_features(random_walk(400, seed=21, vol=0.08))  # extreme volatility: 3R below entry would be negative
    lv = compute_levels(f, len(f) - 1, "SHORT", EngineConfig().levels)
    assert all(t > 0 for t in lv.targets) and lv.targets[0] > lv.targets[1] > lv.targets[2]


def test_precision_and_currency_in_api(app_client, admin_headers, multi):
    c = app_client.get(f"{API}/stocks/DEMO_SHIB/candles?limit=60", headers=admin_headers).json()
    assert c["bars"]["price_decimals"] >= 9 and min(v for v in c["bars"]["close"] if v) > 0  # sub-cent prices survive
    d = app_client.get(f"{API}/stocks/DEMO_EURUSD", headers=admin_headers).json()
    assert d["quote"]["price"] != round(d["quote"]["price"], 2)  # 5 dp, not 2
    wl = app_client.post(f"{API}/watchlists", json={"name": "mm", "market": "CRYPTO"}, headers=admin_headers).json()
    items = app_client.post(f"{API}/watchlists/{wl['id']}/items", json={"symbol": "DEMO_SHIB"}, headers=admin_headers).json()["items"]
    assert items[0]["market"] == "CRYPTO" and items[0]["currency"] == "USD" and items[0]["quote"]["price"] > 0
    app_client.delete(f"{API}/watchlists/{wl['id']}", headers=admin_headers)


def test_multi_currency_portfolio_is_converted(app_client, admin_headers, multi):
    for kind in ("ingest", "scan"):
        r = app_client.post(f"{API}/admin/jobs/{kind}?market=EUROPE" + ("&full=true" if kind == "ingest" else ""), headers=admin_headers)
        assert app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()["status"] == "done"
    r = app_client.post(f"{API}/backtests", json={"market": "EUROPE", "strategy_key": "pullback_ema20", "walk_forward": False}, headers=admin_headers)
    bt = app_client.get(f"{API}/backtests/{r.json()['id']}", headers=admin_headers).json()
    assert bt["status"] == "done", bt["error"]
    pf = bt["result"]["portfolio"]
    assert pf["currency"] == "USD" and pf["fx_conversion"]["applied"] and set(pf["fx_conversion"]["pairs"]) == {"EUR", "GBP"}
    assert pf["fx_conversion"]["pairs"]["EUR"] == ["DEMO_EURUSD"] and not pf["fx_conversion"]["missing"]


def test_rate_series_bridges_via_usd(multi):
    from app.core.db import SessionLocal
    from app.services.fx_service import rate_series

    db = SessionLocal()
    try:
        s, path = rate_series(db, "EUR", "INR")
        direct, _ = rate_series(db, "EUR", "USD")
        assert path == ["DEMO_EURINR"] and len(s) > 1000  # direct pair preferred
        jpy, jpath = rate_series(db, "JPY", "USD")
        assert jpath == ["1/DEMO_USDJPY"] and 0 < float(jpy.iloc[-1]) < 0.02
        krw_inr, kpath = rate_series(db, "KRW", "INR")
        assert kpath == ["1/DEMO_USDKRW", "DEMO_USDINR"] and float(krw_inr.iloc[-1]) > 0
        assert direct is not None
    finally:
        db.close()


def test_binance_universe_filters_pegged_commodity_and_excluded():
    from app.providers.binance import BinanceProvider, looks_pegged

    sym = lambda s, b: {"symbol": s, "status": "TRADING", "quoteAsset": "USDT", "baseAsset": b}  # noqa: E731
    tick = lambda s, last, hi, lo, qv="1000": {"symbol": s, "lastPrice": last, "highPrice": hi, "lowPrice": lo, "quoteVolume": qv}  # noqa: E731
    routes = {
        "/api/v3/exchangeInfo": {"symbols": [sym("BTCUSDT", "BTC"), sym("NEWSTABLEUSDT", "NEWSTABLE"), sym("PAXGUSDT", "PAXG"),
                                             sym("CRCLBUSDT", "CRCLB"), sym("CHEAPUSDT", "CHEAP")]},
        "/api/v3/ticker/24hr": [tick("BTCUSDT", "100000", "101000", "98000"), tick("NEWSTABLEUSDT", "1.0002", "1.0005", "0.9998"),
                                tick("PAXGUSDT", "4000", "4050", "3990"), tick("CRCLBUSDT", "84", "88", "83"),
                                tick("CHEAPUSDT", "0.98", "1.05", "0.93")],  # ~$1 but volatile: a real token, kept
        "/api/v3/ticker/bookTicker": [],
    }
    p = BinanceProvider(universe_size=10, client=_mock(routes), pause_s=0, exclude=["crclb"])
    assert {i.symbol for i in p.list_instruments()} == {"BTCUSDT", "CHEAPUSDT"}
    assert {"NEWSTABLEUSDT", "PAXGUSDT", "CRCLBUSDT"} <= p.rejected
    assert looks_pegged({"lastPrice": "1", "highPrice": "1.001", "lowPrice": "0.999"}) and not looks_pegged({"lastPrice": "1", "highPrice": "1.1", "lowPrice": "0.95"})
