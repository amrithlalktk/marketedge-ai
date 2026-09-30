"""Black-76 pricing on the futures/forward price, Greeks and implied volatility.

NIFTY options are European; Black-76 on the forward F = S·e^{(r−q)T} is used so
the dividend yield and carry are handled consistently. All functions are
vectorised over numpy arrays.

Conventions: T in years (calendar days / 365), vol as a decimal, theta per
CALENDAR day, vega per 1 vol-point (0.01).
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone

import numpy as np

SQRT2 = math.sqrt(2.0)
IST = timezone(timedelta(hours=5, minutes=30))
MARKET_CLOSE = time(15, 30)


def _ncdf(x):
    x = np.asarray(x, dtype=float)
    from math import erf

    return 0.5 * (1.0 + np.vectorize(erf)(x / SQRT2))


def _npdf(x):
    return np.exp(-0.5 * np.asarray(x, dtype=float) ** 2) / math.sqrt(2 * math.pi)


def forward(spot, r: float, q: float, T):
    return np.asarray(spot, dtype=float) * np.exp((r - q) * np.asarray(T, dtype=float))


def year_fraction(as_of: datetime, expiry_date) -> float:
    """Calendar-time to expiry at 15:30 IST."""
    exp_dt = datetime.combine(expiry_date, MARKET_CLOSE, tzinfo=IST)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=IST)
    return max((exp_dt - as_of).total_seconds() / (365 * 86400), 0.0)


def black76(F, K, T, vol, r, is_call):
    F, K, T, vol = (np.asarray(a, dtype=float) for a in (F, K, T, vol))
    is_call = np.asarray(is_call, dtype=bool)
    df = np.exp(-r * T)
    intrinsic = np.where(is_call, np.maximum(F - K, 0.0), np.maximum(K - F, 0.0)) * df
    live = (T > 0) & (vol > 0)
    sT = np.where(live, vol * np.sqrt(np.where(live, T, 1.0)), 1.0)
    d1 = (np.log(F / K) + 0.5 * sT ** 2) / sT
    d2 = d1 - sT
    call = df * (F * _ncdf(d1) - K * _ncdf(d2))
    put = df * (K * _ncdf(-d2) - F * _ncdf(-d1))
    return np.where(live, np.where(is_call, call, put), intrinsic)


def greeks(S, K, T, vol, r, q, is_call) -> dict:
    """Greeks with respect to SPOT (what traders hedge with), from Black-76 on F."""
    S, K, T, vol = (np.asarray(a, dtype=float) for a in (S, K, T, vol))
    is_call = np.asarray(is_call, dtype=bool)
    live = (T > 0) & (vol > 0)
    Tl = np.where(live, T, 1.0)
    F = forward(S, r, q, Tl)
    sT = np.where(live, vol * np.sqrt(Tl), 1.0)
    d1 = (np.log(F / K) + 0.5 * sT ** 2) / sT
    d2 = d1 - sT
    df = np.exp(-r * Tl)
    carry = np.exp(-q * Tl)  # dF/dS * df = e^{(r-q)T} e^{-rT}
    delta = np.where(is_call, carry * _ncdf(d1), carry * (_ncdf(d1) - 1))
    gamma = carry * _npdf(d1) / (S * sT)
    vega = S * carry * _npdf(d1) * np.sqrt(Tl) / 100
    theta_call = (-S * carry * _npdf(d1) * vol / (2 * np.sqrt(Tl)) - r * K * df * _ncdf(d2) + q * S * carry * _ncdf(d1))
    theta_put = (-S * carry * _npdf(d1) * vol / (2 * np.sqrt(Tl)) + r * K * df * _ncdf(-d2) - q * S * carry * _ncdf(-d1))
    theta = np.where(is_call, theta_call, theta_put) / 365
    itm = np.where(is_call, S > K, S < K)
    return {
        "delta": np.where(live, delta, np.where(itm, np.where(is_call, 1.0, -1.0), 0.0)),
        "gamma": np.where(live, gamma, 0.0),
        "theta": np.where(live, theta, 0.0),
        "vega": np.where(live, vega, 0.0),
    }


def implied_vol(price, F, K, T, r, is_call, lo: float = 0.005, hi: float = 3.0, iters: int = 80):
    """Vectorised bisection. Returns NaN where the price is below intrinsic or above the upper bound."""
    price, F, K, T = (np.asarray(a, dtype=float) for a in (price, F, K, T))
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), price.shape)
    a = np.full(price.shape, lo)
    b = np.full(price.shape, hi)
    pa = black76(F, K, T, a, r, is_call) - price
    pb = black76(F, K, T, b, r, is_call) - price
    ok = (pa <= 0) & (pb >= 0) & (T > 0) & (price > 0)
    for _ in range(iters):
        m = 0.5 * (a + b)
        pm = black76(F, K, T, m, r, is_call) - price
        left = pm > 0
        b = np.where(left, m, b)
        a = np.where(left, a, m)
    return np.where(ok, 0.5 * (a + b), np.nan)
