"""SAMPLE / SYNTHETIC data for CRYPTO, US, EUROPE, ASIA and FX — DEVELOPMENT AND TESTS ONLY.

Every symbol is prefixed DEMO_ and flagged is_sample. FX pairs are derived
from one set of synthetic USD legs, so crosses and INR conversions are
internally consistent. Crypto derivatives series (funding, OI, long/short)
are synthetic too. No fundamentals, earnings or macro data are generated.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import _synth as sy
from .base import DataMeta, Instrument, MarketDataProvider

SOURCE = "sample-synthetic"


def _seed(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)


STARTS = {"weekdays": pd.Timestamp("2018-11-01"), "24x7": pd.Timestamp("2020-10-01")}  # fixed → prefix-stable history


def _calendar(today: date, years: float, calendar: str) -> pd.DatetimeIndex:
    if calendar == "24x7":
        end = pd.Timestamp(today) - pd.Timedelta(days=1)  # last COMPLETE UTC day
        return pd.date_range(start=STARTS["24x7"], end=end, freq="D")
    end = pd.Timestamp(today)
    while end.weekday() >= 5:
        end -= pd.Timedelta(days=1)
    return pd.bdate_range(start=STARTS["weekdays"], end=end)


def _regime_market(n: int, rng, scale: float) -> np.ndarray:
    """Regime-switching market factor (bull/range/bear/crash) scaled by `scale`."""
    reg = {"bull": (0.0007, 0.0085), "range": (0.0, 0.008), "bear": (-0.0007, 0.014), "crash": (-0.004, 0.028)}
    trans = {"bull": [0.985, 0.01, 0.004, 0.001], "range": [0.012, 0.975, 0.012, 0.001],
             "bear": [0.006, 0.012, 0.978, 0.004], "crash": [0.05, 0.05, 0.1, 0.8]}
    names = list(reg)
    u, z = rng.random(n), rng.standard_normal(n)
    st, out = "bull", np.empty(n)
    for i in range(n):
        mu, vol = reg[st]
        out[i] = scale * (mu + vol * z[i])
        cum = np.cumsum(trans[st])
        st = names[min(int(np.searchsorted(cum, u[i] * cum[-1])), 3)]
    return out


def _bars(dates, r: np.ndarray, base: float, vol: float, volume_base: float, rs, decimals: int = 2) -> pd.DataFrame:
    n = len(r)
    close = base * np.exp(np.cumsum(r))
    prev = np.concatenate([[base], close[:-1]])
    open_ = prev * np.exp(rs.normal(0, 0.25 * vol, n))
    hi = np.maximum(open_, close) * np.exp(np.abs(rs.normal(0, 0.5 * vol, n)))
    lo = np.minimum(open_, close) * np.exp(-np.abs(rs.normal(0, 0.5 * vol, n)))
    volume = volume_base * np.exp(rs.normal(0, 0.3, n)) * (1 + 0.8 * np.abs(r) / max(vol, 1e-6)) if volume_base > 0 else np.zeros(n)
    df = pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close, "volume": np.round(volume)}, index=dates)
    return df.round({"open": decimals, "high": decimals, "low": decimals, "close": decimals})


def _trend_episodes(n: int, rs, strength: float) -> np.ndarray:
    t = np.zeros(n)
    i = 0
    while i < n:
        if rs.random() < 0.01:
            L = int(rs.integers(15, 60))
            t[i : i + L] = rs.normal(strength, strength * 0.6) * (1 if rs.random() < 0.6 else -1)
            i += L
        else:
            i += 1
    return t


@dataclass
class _Built:
    instruments: List[Instrument]
    bars: Dict[str, pd.DataFrame]
    derivatives: Dict[str, pd.DataFrame]


# ----------------------------------------------------------------------------- equities
_EQUITY_SPECS = {
    "US": {"bench": ("DEMO_US500", "DEMO US 500 (synthetic)", 4800.0), "vix": "DEMO_VIX_US", "n": 30, "ccy": {"NYSE": "USD", "NASDAQ": "USD"},
           "sectors": ["Technology", "Financials", "Healthcare", "Consumer Discretionary", "Industrials", "Energy", "Communication", "Consumer Staples"],
           "px": (20, 600), "vol": (2e6, 3e7), "etfs": ["DEMO_US_SPY", "DEMO_US_QQQ", "DEMO_US_IWM", "DEMO_US_XLF", "DEMO_US_XLE"]},
    "EUROPE": {"bench": ("DEMO_EU600", "DEMO EUROPE 600 (synthetic)", 480.0), "vix": None, "n": 15,
               "ccy": {"LSE": "GBP", "XETRA": "EUR", "EURONEXT": "EUR"},
               "sectors": ["Banks", "Industrials", "Consumer", "Healthcare", "Energy", "Technology"], "px": (5, 300), "vol": (5e5, 8e6), "etfs": []},
    "ASIA": {"bench": ("DEMO_ASIA50", "DEMO ASIA 50 (synthetic)", 3200.0), "vix": None, "n": 16,
             "ccy": {"TSE": "JPY", "HKEX": "HKD", "SGX": "SGD", "KRX": "KRW"},
             "sectors": ["Technology", "Financials", "Industrials", "Consumer", "Real Estate", "Autos"],
             "px": {"JPY": (800, 12000), "HKD": (10, 400), "SGD": (1, 40), "KRW": (20000, 400000)},
             "vol": {"JPY": (3e5, 5e6), "HKD": (2e6, 3e7), "SGD": (5e5, 1e7), "KRW": (1e5, 3e6)}, "etfs": []},
}


def _equity_market(market: str, today: date) -> _Built:
    spec = _EQUITY_SPECS[market]
    dates = _calendar(today, 8, "weekdays")
    n = len(dates)
    mkt, _ = sy.regime_market(f"{market}-mkt", n, 1.0)
    inst, bars = [], {}
    sym, name, base = spec["bench"]
    bars[sym] = sy.ohlc(sym, dates, mkt, base, 0.009 * 0.25, 0)
    inst.append(Instrument(sym, name, "INDEX", list(spec["ccy"])[0], "USD" if market != "EUROPE" else "EUR", None, is_index=True, is_sample=True))
    if spec["vix"]:
        vix = (100 * np.sqrt(252) * pd.Series(mkt, index=dates).rolling(20, min_periods=2).std().fillna(0.009) * 1.1).ewm(span=5, adjust=False).mean()
        bars[spec["vix"]] = pd.DataFrame({"open": vix, "high": vix * 1.03, "low": vix * 0.97, "close": vix, "volume": 0.0}, index=dates)
        inst.append(Instrument(spec["vix"], "DEMO US VOLATILITY INDEX (synthetic)", "INDEX", "NYSE", "USD", None, is_index=True, is_sample=True))
    exchanges = list(spec["ccy"])
    sectors = spec["sectors"]
    sector_ret = {s: sy.draw(f"{market}{s}", "normal", n, 0, 0.005) for s in sectors}
    for k in range(spec["n"]):
        s = f"DEMO_{market[:2]}_{k + 1:03d}"
        ex = exchanges[k % len(exchanges)]
        ccy = spec["ccy"][ex]
        sec = sectors[k % len(sectors)]
        beta, idio = sy.scalar(s + "-beta", 0.7, 1.4), sy.scalar(s + "-idio", 0.011, 0.022)
        r = beta * mkt + sector_ret[sec] + sy.trend_episodes(s, n, 0.0012) + sy.draw(s + "-idio", "normal", n, 0, idio)
        px_rng = spec["px"][ccy] if isinstance(spec["px"], dict) else spec["px"]
        vol_rng = spec["vol"][ccy] if isinstance(spec["vol"], dict) else spec["vol"]
        dec = 0 if ccy in ("JPY", "KRW") else 2
        bars[s] = sy.ohlc(s, dates, r, sy.scalar(s + "-px", *px_rng), idio, sy.scalar(s + "-volbase", *vol_rng), dec)
        inst.append(Instrument(s, f"Demo {market.title()} Company {k + 1:03d} (synthetic)", "EQUITY", ex, ccy, sec, is_sample=True))
    for e in spec["etfs"]:
        r = sy.scalar(e + "-beta", 0.8, 1.3) * mkt + sy.draw(e + "-idio", "normal", n, 0, 0.004)
        bars[e] = sy.ohlc(e, dates, r, sy.scalar(e + "-px", 50, 450), 0.01, sy.scalar(e + "-volbase", 2e7, 8e7))
        inst.append(Instrument(e, f"{e.replace('DEMO_US_', 'Demo ')} ETF (synthetic)", "ETF", "NYSE", "USD", "ETF", is_sample=True))
    return _Built(inst, bars, {})


# ----------------------------------------------------------------------------- crypto
_COINS = [("BTC", "L1", 60000, 1.2e12), ("ETH", "L1", 3000, 3.8e11), ("SOL", "L1", 150, 7e10), ("XRP", "Payments", 0.6, 3.3e10),
          ("BNB", "Exchange", 580, 8.5e10), ("ADA", "L1", 0.45, 1.6e10), ("AVAX", "L1", 35, 1.3e10), ("LINK", "Oracle", 15, 9e9),
          ("DOGE", "Meme", 0.15, 2.1e10), ("DOT", "L1", 7, 9.5e9), ("TRX", "L1", 0.12, 1.05e10), ("LTC", "Payments", 80, 6e9),
          ("MATIC", "L2", 0.7, 6.5e9), ("UNI", "DeFi", 8, 5e9), ("ATOM", "L1", 8, 3e9), ("AAVE", "DeFi", 110, 1.6e9),
          ("ARB", "L2", 0.9, 3e9), ("OP", "L2", 2, 2.2e9), ("SHIB", "Meme", 0.00002, 1.2e10), ("ILLIQ", "Meme", 0.02, 4e7)]


def _crypto_market(today: date) -> _Built:
    dates = _calendar(today, 6, "24x7")
    n = len(dates)
    btc_r, _ = sy.regime_market("crypto-mkt", n, 3.2)
    inst, bars, deriv = [], {}, {}
    for tk, sec, px, mcap in _COINS:
        s = f"DEMO_{tk}"
        if tk == "BTC":
            r = btc_r
            vol = 0.03
        else:
            beta, idio = sy.scalar(s + "-beta", 1.0, 1.6), sy.scalar(s + "-idio", 0.025, 0.05)
            r = beta * btc_r + sy.trend_episodes(s, n, 0.003) + sy.draw(s + "-idio", "normal", n, 0, idio)
            vol = idio
        anchor = min(1800, n - 1)  # a fixed bar inside every history: price there ≈ the nominal level (prefix-stable)
        start_px = px / float(np.exp(np.cumsum(r)[anchor]))
        usd_vol = (mcap * sy.scalar(s + "-turnover", 0.02, 0.06)) if tk != "ILLIQ" else 5e5
        df = sy.ohlc(s, dates, r, start_px, vol, usd_vol / px, 8 if px < 1 else 2)
        bars[s] = df
        inst.append(Instrument(s, f"Demo {tk} (synthetic)", "CRYPTO", "CRYPTO", "USD", sec, is_sample=True,
                               extra={"market_cap_usd": float(mcap), "quote": "USDT", "base": tk,
                                      "spread_bps": float(sy.scalar(s + "-spread", 1, 6) if tk != "ILLIQ" else 80)}))
        # synthetic perpetual-futures context (funding follows recent momentum; OI trends with price)
        ret7 = pd.Series(np.log(df["close"])).diff(7).fillna(0).to_numpy()
        funding = np.clip(0.0001 + 0.002 * ret7 + sy.draw(s + "-fund", "normal", n, 0, 0.00008), -0.002, 0.003)
        oi = mcap * 0.02 * np.exp(np.cumsum(0.6 * r + sy.draw(s + "-oi", "normal", n, 0, 0.01)) * 0.5)
        ls = np.clip(1.2 + 3 * ret7 + sy.draw(s + "-ls", "normal", n, 0, 0.1), 0.4, 4.0)
        if tk != "ILLIQ":
            deriv[s] = pd.DataFrame({"funding_rate": funding, "open_interest": oi, "long_short_ratio": ls}, index=dates).iloc[-60:]
    return _Built(inst, bars, deriv)


# ----------------------------------------------------------------------------- forex
_LEGS = {  # currency: (USD per unit start, daily vol)
    "EUR": (1.10, 0.0045), "GBP": (1.27, 0.0055), "JPY": (1 / 150, 0.006), "CHF": (1 / 0.88, 0.005), "AUD": (0.66, 0.0065),
    "CAD": (1 / 1.36, 0.0045), "NZD": (0.61, 0.0068), "INR": (1 / 83.0, 0.0025), "HKD": (1 / 7.8, 0.0003), "SGD": (1 / 1.34, 0.0035),
    "KRW": (1 / 1330.0, 0.005),
}
_PAIRS = [("EUR", "USD"), ("GBP", "USD"), ("USD", "JPY"), ("USD", "CHF"), ("AUD", "USD"), ("USD", "CAD"), ("NZD", "USD"), ("EUR", "GBP"),
          ("USD", "INR"), ("EUR", "INR"), ("GBP", "INR"), ("USD", "HKD"), ("USD", "SGD"), ("USD", "KRW")]


def _fx_market(today: date) -> _Built:
    dates = _calendar(today, 8, "weekdays")
    n = len(dates)
    usd = {"USD": np.ones(n)}
    dxy_shock = sy.draw("usd-factor", "normal", n, 0, 0.003)
    for c, (start, vol) in _LEGS.items():
        r = -0.8 * dxy_shock + sy.trend_episodes("leg" + c, n, vol * 0.08) + sy.draw("leg" + c, "normal", n, 0, vol)
        usd[c] = start * np.exp(np.cumsum(r))
    inst, bars = [], {}
    for b, q in _PAIRS:
        s = f"DEMO_{b}{q}"
        close = usd[b] / usd[q]
        vol = _LEGS.get(b, (1, 0.004))[1] if b != "USD" else _LEGS.get(q, (1, 0.004))[1]  # fixed per pair (not estimated from the path)
        prev = np.concatenate([[close[0]], close[:-1]])
        o = prev * np.exp(sy.draw(s + "-gap", "normal", n, 0, 0.1 * vol))
        hi = np.maximum(o, close) * np.exp(np.abs(sy.draw(s + "-hi", "normal", n, 0, 0.5 * vol)))
        lo = np.minimum(o, close) * np.exp(-np.abs(sy.draw(s + "-lo", "normal", n, 0, 0.5 * vol)))
        dec = 3 if q in ("JPY", "INR") else 1 if q == "KRW" else 5
        bars[s] = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": close, "volume": 0.0}, index=dates).round(dec)
        group = "INR" if "INR" in (b, q) else "Major" if "USD" in (b, q) else "Cross"
        inst.append(Instrument(s, f"Demo {b}/{q} (synthetic)", "FOREX", "FX", q, group, is_sample=True, extra={"base": b, "quote": q}))
    dxy = 100 * np.exp(np.cumsum(dxy_shock))
    bars["DEMO_DXY"] = pd.DataFrame({"open": dxy, "high": dxy * 1.002, "low": dxy * 0.998, "close": dxy, "volume": 0.0}, index=dates).round(3)
    inst.append(Instrument("DEMO_DXY", "DEMO US DOLLAR INDEX (synthetic)", "INDEX", "FX", "USD", None, is_index=True, is_sample=True))
    return _Built(inst, bars, {})


_CACHE: Dict[Tuple[str, date], _Built] = {}


def build(market: str, today: Optional[date] = None) -> _Built:
    today = today or date.today()
    key = (market, today)
    if key not in _CACHE:
        _CACHE[key] = _crypto_market(today) if market == "CRYPTO" else _fx_market(today) if market == "FX" else _equity_market(market, today)
    return _CACHE[key]


class SampleGlobalProvider(MarketDataProvider):
    name = "sample"
    is_sample = True

    def __init__(self, market: str, today: Optional[date] = None):
        self.market, self.today = market, today

    def list_instruments(self, market: str = None) -> List[Instrument]:
        return list(build(market or self.market, self.today).instruments)

    def get_ohlcv(self, symbol: str, interval: str = "1d", start: Optional[date] = None, end: Optional[date] = None):
        if interval != "1d":
            raise NotImplementedError("Sample global provider serves daily bars only")
        df = build(self.market, self.today).bars.get(symbol)
        if df is None:
            raise KeyError(symbol)
        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        return df.copy(), DataMeta(source=SOURCE, is_sample=True, as_of=str(df.index[-1].date()) if len(df) else None,
                                   fetched_at=datetime.now(timezone.utc).isoformat(), note="SAMPLE DATA — synthetic prices. Not real market data.")

    def get_derivatives(self, symbol: str) -> Optional[pd.DataFrame]:
        d = build(self.market, self.today).derivatives.get(symbol)
        return None if d is None else d.copy()

    def health(self) -> dict:
        return {"provider": f"sample:{self.market}", "ok": True, "is_sample": True}
