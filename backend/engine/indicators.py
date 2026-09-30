"""Technical indicators implemented from their textbook definitions.

All functions are causal: the value at index t depends only on rows <= t.
`tests/test_indicators.py` verifies this property by recomputing on truncated
inputs. Wilder smoothing (RMA) is used where the original author specified it
(RSI, ATR, ADX).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's moving average (alpha = 1/n)."""
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = rma(delta.clip(lower=0), n)
    loss = rma(-delta.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(~((loss == 0) & gain.notna()), 100.0)
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), n)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.DataFrame:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr_s = rma(true_range(high, low, close), n)
    plus_di = 100 * rma(plus_dm, n) / tr_s.replace(0, np.nan)
    minus_di = 100 * rma(minus_dm, n) / tr_s.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"adx": rma(dx, n), "plus_di": plus_di, "minus_di": minus_di})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    width = (upper - lower) / mid
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_width": width, "bb_pctb": pct_b})


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14, d: int = 3) -> pd.DataFrame:
    ll = low.rolling(n, min_periods=n).min()
    hh = high.rolling(n, min_periods=n).max()
    k = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    return pd.DataFrame({"stoch_k": k, "stoch_d": sma(k, d)})


def cci(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 20) -> pd.Series:
    tp = (high + low + close) / 3
    ma = sma(tp, n)
    md = tp.rolling(n, min_periods=n).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def roc(close: pd.Series, n: int = 12) -> pd.Series:
    return 100 * (close / close.shift(n) - 1)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, n: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    flow = tp * volume
    pos = flow.where(tp > tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    neg = flow.where(tp < tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    ratio = pos / neg.replace(0, np.nan)
    out = 100 - 100 / (1 + ratio)
    return out.where(~((neg == 0) & pos.notna()), 100.0)


def rolling_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, n: int = 20) -> pd.Series:
    """Rolling n-bar VWAP. On daily bars a session VWAP is meaningless, so the
    swing engine uses this rolling variant; intraday uses `session_vwap`."""
    tp = (high + low + close) / 3
    return (tp * volume).rolling(n, min_periods=n).sum() / volume.rolling(n, min_periods=n).sum().replace(0, np.nan)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.normalize()
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vv = df["volume"].groupby(day).cumsum()
    return pv / vv.replace(0, np.nan)


def supertrend(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    a = atr(high, low, close, n).to_numpy()
    h, l, c = high.to_numpy(), low.to_numpy(), close.to_numpy()
    hl2 = (h + l) / 2
    upper_basic, lower_basic = hl2 + mult * a, hl2 - mult * a
    size = len(c)
    upper, lower = np.full(size, np.nan), np.full(size, np.nan)
    st, direction = np.full(size, np.nan), np.zeros(size)
    for i in range(size):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(upper[i - 1]):
            upper[i], lower[i] = upper_basic[i], lower_basic[i]
            direction[i] = 1 if c[i] >= hl2[i] else -1
        else:
            upper[i] = upper_basic[i] if (upper_basic[i] < upper[i - 1] or c[i - 1] > upper[i - 1]) else upper[i - 1]
            lower[i] = lower_basic[i] if (lower_basic[i] > lower[i - 1] or c[i - 1] < lower[i - 1]) else lower[i - 1]
            if direction[i - 1] == -1 and c[i] > upper[i - 1]:
                direction[i] = 1
            elif direction[i - 1] == 1 and c[i] < lower[i - 1]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]
        st[i] = lower[i] if direction[i] == 1 else upper[i]
    return pd.DataFrame({"supertrend": st, "supertrend_dir": direction}, index=close.index)


def rolling_percentile_rank(s: pd.Series, n: int) -> pd.Series:
    """Fraction of the trailing n values (inclusive) that are <= the current value."""
    return s.rolling(n, min_periods=max(20, n // 4)).apply(lambda x: (x <= x[-1]).mean(), raw=True)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of an OHLCV frame enriched with every indicator the engine uses."""
    out = df.copy()
    h, l, c, v = out["high"], out["low"], out["close"], out["volume"]
    for n in (20, 50, 100, 200):
        out[f"ema{n}"] = ema(c, n)
    out["sma20"], out["sma50"], out["sma200"] = sma(c, 20), sma(c, 50), sma(c, 200)
    out["rsi"] = rsi(c)
    out = out.join(macd(c))
    out = out.join(adx(h, l, c))
    out["atr"] = atr(h, l, c)
    out["atr_pct"] = 100 * out["atr"] / c
    out["atr_pct_rank"] = rolling_percentile_rank(out["atr_pct"], 252)
    out = out.join(bollinger(c))
    out["bb_width_rank"] = rolling_percentile_rank(out["bb_width"], 120)
    out = out.join(stochastic(h, l, c))
    out["cci"] = cci(h, l, c)
    out["roc"] = roc(c)
    out["obv"] = obv(c, v)
    out["obv_slope"] = out["obv"].diff(10)
    out["mfi"] = mfi(h, l, c, v)
    out["vwap20"] = rolling_vwap(h, l, c, v, 20)
    out = out.join(supertrend(h, l, c))
    out["vol_sma20"], out["vol_sma50"] = sma(v, 20), sma(v, 50)
    out["vol_ratio20"] = v / out["vol_sma20"].shift(1)
    out["vol_ratio50"] = v / out["vol_sma50"].shift(1)
    out["traded_value"] = c * v
    out["avg_traded_value20"] = sma(out["traded_value"], 20)
    out["hh20_prior"] = h.rolling(20, min_periods=20).max().shift(1)
    out["ll20_prior"] = l.rolling(20, min_periods=20).min().shift(1)
    out["high_52w"] = h.rolling(252, min_periods=200).max()
    out["low_52w"] = l.rolling(252, min_periods=200).min()
    out["ret1"] = c.pct_change(fill_method=None)
    out["ret5"] = c.pct_change(5, fill_method=None)
    out["ret20"] = c.pct_change(20, fill_method=None)
    out["ret63"] = c.pct_change(63, fill_method=None)
    out["ema200_slope"] = out["ema200"].pct_change(20, fill_method=None)
    rng = (h - l).replace(0, np.nan)
    out["close_loc"] = (c - l) / rng  # 1.0 = closed at the high
    return out
