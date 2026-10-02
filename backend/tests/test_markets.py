"""Markets (NSE + CRYPTO): calendars, precision, crypto derivatives context, Binance adapter, geometric patterns,
historical scoring component, crypto pipeline API and signal-outcome de-duplication."""
from __future__ import annotations

from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest

from engine.crypto import derivatives_context
from engine.features import build_features
from engine.levels import price_decimals
from engine.markets import PROFILES, profile
from engine.scoring import score_volume
from engine.validation import expected_last_session

API = "/api/v1"


def test_calendars_and_profiles():
    wed, sat = date(2026, 9, 30), date(2026, 10, 3)
    assert expected_last_session(sat) == date(2026, 10, 2)  # weekday market → Friday
    assert expected_last_session(sat, calendar="24x7") == date(2026, 10, 2)  # yesterday's complete UTC day
    assert expected_last_session(wed, calendar="24x7") == date(2026, 9, 29)
    assert profile("CRYPTO").periods_per_year == 365 and set(PROFILES) == {"NSE", "CRYPTO"}


def test_price_precision_matches_instrument_scale():
    assert price_decimals(25000) == 2 and price_decimals(1.0842) == 5 and price_decimals(0.00002) >= 9


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


def test_short_targets_never_below_zero():
    from engine.config import EngineConfig
    from engine.features import build_features
    from engine.levels import compute_levels
    from tests.test_engine_core import random_walk

    f = build_features(random_walk(400, seed=21, vol=0.08))  # extreme volatility: 3R below entry would be negative
    lv = compute_levels(f, len(f) - 1, "SHORT", EngineConfig().levels)
    assert all(t > 0 for t in lv.targets) and lv.targets[0] > lv.targets[1] > lv.targets[2]


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


def _ohlc(close, vol=1e6, start="2023-01-02"):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": close, "high": close + 0.3, "low": close - 0.3, "close": close, "volume": vol},
                        index=pd.bdate_range(start, periods=len(close)))


def test_ascending_triangle_breakout_detected_without_look_ahead():
    rng = np.random.default_rng(0)
    x = np.arange(200)
    lows = np.linspace(95, 108, 200)
    close = np.where(np.sin(x / 6) > 0, lows + (110 - lows) * np.sin(x / 6), lows + (110 - lows) * 0.1)
    close = np.concatenate([100 + rng.normal(0, 0.3, 60), close, np.linspace(110.5, 118, 10)])
    f = build_features(_ohlc(close))
    assert f["pat_triangle_asc"].sum() > 20
    brk = f.index[f["pat_geo_breakout_up"]]
    assert any(d >= f.index[260] for d in brk)  # breakout of the final flat top
    cut = 255
    part = build_features(_ohlc(close[: cut + 1]))
    for c in ("pat_triangle_asc", "pat_rectangle", "pat_geo_breakout_up", "pat_cup_handle"):
        assert bool(f[c].iloc[cut]) == bool(part[c].iloc[cut]), c


def test_cup_and_handle_detected():
    rng = np.random.default_rng(3)
    base = np.linspace(80, 99, 60)                                   # advance into the left rim
    left_rim = np.array([99.5, 100.0, 99.4])                          # a real (unique) peak
    cup = 100 - 22 * np.sin(np.linspace(0.05, np.pi - 0.05, 80))     # rounded ~22% cup
    right_rim = np.array([99.2, 99.8, 99.1])
    handle = np.linspace(98.6, 95.5, 6).tolist() + np.linspace(95.8, 99.3, 6).tolist()  # shallow handle (< 1/3 depth)
    brk = np.linspace(100.6, 105, 5)
    close = np.concatenate([base, left_rim, cup, right_rim, handle, brk]) + rng.normal(0, 0.05, 60 + 3 + 80 + 3 + 12 + 5)
    f = build_features(_ohlc(close, vol=1e6, start="2022-01-03"))
    assert f["pat_cup_handle"].any()
    assert f["pat_cup_handle_breakout"].iloc[-6:].any()


def test_historical_component_is_visible_but_unweighted_by_default():
    from engine.scoring import combine, score_historical
    from engine.config import DEFAULT_WEIGHTS

    assert DEFAULT_WEIGHTS["historical"] == 0 and DEFAULT_WEIGHTS["ml"] == 0
    strong = score_historical({"sample_size": 300, "expectancy_r": 0.4, "t1_hit_rate": 55})
    weak = score_historical({"sample_size": 300, "expectancy_r": -0.3, "t1_hit_rate": 30})
    thin = score_historical({"sample_size": 35, "expectancy_r": 0.4, "t1_hit_rate": 55})
    assert strong > 80 > 50 > weak and 50 < thin < strong and score_historical({"sample_size": 10, "expectancy_r": 1}) is None
    comps = {"trend": 80.0, "historical": 20.0}
    assert combine(comps, {"trend": 1, "historical": 0}) == 80.0 and combine(comps, {"trend": 1, "historical": 1}) == 50.0

# ------------------------------------------------------------------ API (sample data)
@pytest.fixture(scope="module")
def crypto(app_client, admin_headers, scanned):
    for kind in ("ingest", "scan"):
        r = app_client.post(f"{API}/admin/jobs/{kind}?market=CRYPTO" + ("&full=true" if kind == "ingest" else ""), headers=admin_headers)
        assert r.status_code == 202
        assert app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()["status"] == "done"
    return True


def test_markets_listing_and_crypto_endpoints(app_client, admin_headers, crypto):
    d = app_client.get(f"{API}/markets", headers=admin_headers).json()
    assert [m["id"] for m in d["items"]] == ["NSE", "CRYPTO"] and d["default_market"] in ("NSE", "CRYPTO")
    assert app_client.get(f"{API}/crypto/signals", headers=admin_headers).status_code == 200
    ov = app_client.get(f"{API}/markets/overview?market=CRYPTO", headers=admin_headers).json()
    assert ov["market"] == "CRYPTO" and "regime_ml" not in ov
    for gone in ("/global/signals", "/forex/signals", "/news", "/backtests", "/strategies", "/ml/models", "/analyst/ask"):
        assert app_client.get(API + gone, headers=admin_headers).status_code in (404, 405), gone


def test_signal_history_and_track_record_deduplicate(app_client, admin_headers, crypto):
    for _ in range(2):  # rescan the same session twice
        r = app_client.post(f"{API}/admin/jobs/scan?market=CRYPTO", headers=admin_headers)
        assert app_client.get(f"{API}/admin/jobs/{r.json()['job_id']}", headers=admin_headers).json()["status"] == "done"
    from sqlalchemy import func, select

    from app.core.db import SessionLocal
    from app.models import Signal, SignalOutcome

    db = SessionLocal()
    try:
        rows = db.execute(select(Signal.symbol, Signal.strategy_key, Signal.as_of, func.count()).join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
                          .where(Signal.market == "CRYPTO").group_by(Signal.symbol, Signal.strategy_key, Signal.as_of)).all()
    finally:
        db.close()
    assert all(r[3] == 1 for r in rows)  # one tracked outcome per published setup
    hist = app_client.get(f"{API}/stocks/DEMO_BTC/analysis", headers=admin_headers).json()["signal_history"]
    keys = [(h["as_of"], h["strategy"]) for h in hist]
    assert len(keys) == len(set(keys))


def test_portfolio_refuses_other_currency(app_client, admin_headers, crypto):
    pid = app_client.post(f"{API}/portfolios", json={"name": "INR only", "base_currency": "INR"}, headers=admin_headers).json()["id"]
    r = app_client.post(f"{API}/portfolios/{pid}/orders", json={"symbol": "DEMO_BTC", "direction": "LONG", "quantity": 1}, headers=admin_headers)
    assert r.status_code == 422 and "USD portfolio" in r.text
    usd = app_client.post(f"{API}/portfolios", json={"name": "Crypto USD", "base_currency": "USD"}, headers=admin_headers).json()["id"]
    assert app_client.post(f"{API}/portfolios/{usd}/orders", json={"symbol": "DEMO_BTC", "direction": "LONG", "quantity": 1},
                           headers=admin_headers).status_code == 201
