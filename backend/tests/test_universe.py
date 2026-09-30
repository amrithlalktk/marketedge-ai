"""Point-in-time universe (survivorship-bias removal)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.universe import current_members, pegged_mask, top_n_membership


def _bars(tv_by_day, price=10.0, start="2024-01-01", vol_pct=0.02, seed=0):
    n = len(tv_by_day)
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="D")
    close = price * np.exp(np.cumsum(rng.normal(0, vol_pct, n)))
    vol = np.asarray(tv_by_day, dtype=float) / close
    return pd.DataFrame({"open": close, "high": close * (1 + vol_pct), "low": close * (1 - vol_pct), "close": close, "volume": vol}, index=idx)


def test_membership_is_point_in_time_and_ranked():
    n = 80
    a = _bars(np.full(n, 100.0), seed=1)                                   # always big
    b = _bars(np.r_[np.full(40, 1.0), np.full(40, 1000.0)], seed=2)        # becomes big on day 40
    c = _bars(np.full(n, 10.0), seed=3)
    m = top_n_membership({"A": a, "B": b, "C": c}, n=2, window=5, min_history=5)
    assert not m["A"].iloc[:5].any()                                       # needs min_history bars (+1 shift)
    assert m["A"].iloc[10] and m["C"].iloc[10] and not m["B"].iloc[10]     # before B's volume jump: A, C
    # B's jump on day 40 must not be visible on day 40 itself (decided from data up to t-1)
    assert not m["B"].iloc[40]
    assert m["B"].iloc[50] and m["A"].iloc[50] and not m["C"].iloc[50]
    # truncating the future never changes past membership (no look-ahead)
    cut = {k: v.iloc[:45] for k, v in {"A": a, "B": b, "C": c}.items()}
    m2 = top_n_membership(cut, n=2, window=5, min_history=5)
    for k in m2:
        pd.testing.assert_series_equal(m2[k], m[k].iloc[:45])
    assert current_members(m) == {"A", "B"}


def test_pegged_coins_are_never_members():
    n = 60
    stable = _bars(np.full(n, 1e9), price=1.0, vol_pct=0.0005, seed=4)     # huge volume, $1 peg
    coin = _bars(np.full(n, 10.0), price=1.0, vol_pct=0.03, seed=5)        # ~$1 but volatile: a real token
    assert pegged_mask(stable).iloc[20:].all() and not pegged_mask(coin).iloc[20:].any()
    m = top_n_membership({"STABLE": stable, "COIN": coin}, n=1, window=5, min_history=5)
    assert not m["STABLE"].any() and m["COIN"].iloc[20:].all()


def test_open_trade_on_delisted_series_is_closed_not_dropped():
    from engine.backtest import simulate_trade
    from engine.config import BacktestConfig

    idx = pd.date_range("2024-01-01", periods=6, freq="D")
    f = pd.DataFrame({"open": [100, 100, 99, 97, 95, 94.0], "high": [101, 101, 100, 98, 96, 95.0], "low": [99, 99, 97, 95, 93, 92.0],
                      "close": [100, 100, 98, 96, 94, 93.0], "atr": 3.0}, index=idx)
    args = (f, 0, "LONG", 85.0, 110.0, 120.0, BacktestConfig(max_hold_bars=30), 0.5, "X", "s")
    assert simulate_trade(*args) is None                          # live series: outcome unknown → excluded
    tr = simulate_trade(*args, data_final=True)                   # delisted: the loss is recorded
    assert tr.exit_reason == "delisted" and tr.exit_price == 93.0 and tr.r_multiple < 0


def test_membership_filters_signals_before_simulation():
    from engine.analyzer import Analyzer

    rng = np.random.default_rng(11)
    idx = pd.bdate_range("2021-01-04", periods=400)
    bars = {}
    for i in range(3):
        close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, 400)))
        bars[f"S{i}"] = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                                      "volume": rng.integers(2e5, 2e6, 400).astype(float)}, index=idx)
    a = Analyzer()
    feats = a.prepare(bars)
    ctx = a.market_context(bars["S0"], feats)
    full = a.build_events(feats, ctx.regime_df)
    half = {k: pd.Series(idx >= idx[250], index=idx) for k in feats}         # members only from bar 250
    part = a.build_events(feats, ctx.regime_df, membership=half)
    assert len(full) > len(part) > 0
    assert (pd.to_datetime(part["signal_date"]) >= idx[250]).all()
    none = a.build_events(feats, ctx.regime_df, membership={k: pd.Series(False, index=idx) for k in feats})
    assert none.empty
    b = a.market_context(bars["S0"], feats, membership=half).breadth_df
    assert b["members"].iloc[260] > 0 and b["members"].iloc[100] == 0


def test_binance_archive_delisted_pool():
    import io
    import zipfile

    import httpx

    from app.providers.binance import BinanceProvider

    def zbytes(rows, header=False):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x.csv", ("open_time,open,high,low,close,volume\n" if header else "") + "\n".join(",".join(map(str, r)) for r in rows))
        return buf.getvalue()

    day = 86_400_000
    t0 = int(pd.Timestamp("2022-01-01").timestamp() * 1000)
    early = [[t0 + i * day, 10, 11, 9, 10, 100, 0] for i in range(3)]
    late_us = [[(t0 + (200 + i) * day) * 1000, 1, 1.1, 0.9, 1, 5, 0] for i in range(3)]  # microseconds, after a 197-day gap
    files = {"/data/spot/monthly/klines/DEADUSDT/1d/DEADUSDT-1d-2022-01.zip": zbytes(early),
             "/data/spot/monthly/klines/DEADUSDT/1d/DEADUSDT-1d-2022-07.zip": zbytes(late_us, header=True)}

    def listing(prefix, marker):
        if prefix == "data/spot/monthly/klines/":
            if not marker:  # page 1 of 2
                return ("<IsTruncated>true</IsTruncated><NextMarker>data/spot/monthly/klines/BTCUSDT/</NextMarker>"
                        "<CommonPrefixes><Prefix>data/spot/monthly/klines/BTCUSDT/</Prefix></CommonPrefixes>")
            return ("<IsTruncated>false</IsTruncated>"
                    + "".join(f"<CommonPrefixes><Prefix>data/spot/monthly/klines/{x}/</Prefix></CommonPrefixes>"
                              for x in ("DEADUSDT", "ETHUPUSDT", "DEADBTC", "OLDSTABLEUSDT")))
        if prefix == "data/spot/monthly/klines/DEADUSDT/1d/":
            return "<IsTruncated>false</IsTruncated>" + "".join(f"<Key>{k.lstrip('/')}</Key><Key>{k.lstrip('/')}.CHECKSUM</Key>" for k in files)
        return "<IsTruncated>false</IsTruncated>"

    def handler(req: httpx.Request):
        u = req.url
        if u.host.startswith("s3-"):
            return httpx.Response(200, text=listing(u.params.get("prefix"), u.params.get("marker")))
        if u.host == "data.binance.vision":
            return httpx.Response(200, content=files[u.path])
        routes = {"/api/v3/exchangeInfo": {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "baseAsset": "BTC"}]},
                  "/api/v3/ticker/24hr": [{"symbol": "BTCUSDT", "lastPrice": "100", "highPrice": "105", "lowPrice": "95", "quoteVolume": "9"}],
                  "/api/v3/ticker/bookTicker": []}
        return httpx.Response(200, json=routes[u.path])

    p = BinanceProvider(universe_size=1, client=httpx.Client(transport=httpx.MockTransport(handler)), pause_s=0, pit_universe=True,
                        archive_threads=2, exclude=["OLDSTABLE"])
    inst = {i.symbol: i for i in p.list_instruments()}
    assert set(inst) == {"BTCUSDT", "DEADUSDT"}  # leveraged, non-USDT and excluded pairs are not in the pool
    d = inst["DEADUSDT"]
    assert d.listed_on == "2022-01-01" and d.delisted_on == "2022-07-31" and d.extra["status"] == "delisted"
    df, meta = p.get_ohlcv("DEADUSDT")
    assert meta.source == "binance-archive" and not meta.is_sample
    assert len(df) == 3 and df.index[0] == pd.Timestamp("2022-01-01") + pd.Timedelta(days=200)  # µs parsed; pre-gap segment dropped
    assert p.get_ohlcv("DEADUSDT", start=pd.Timestamp("2022-07-20").date())[0].empty  # final history is not re-downloaded
    assert p.get_derivatives("DEADUSDT") is None
