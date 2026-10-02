"""Upstox API adapter: NSE equities + indices (daily candles) and the NIFTY option chain.

Personal use with the account holder's own Upstox developer app (free with an Upstox account).

Authentication (upstox.com/developer/api-documentation/authentication): OAuth2 authorization-code.
Access tokens expire at 03:30 IST the next day and there is no refresh token, so the user connects
once per day from Admin → Providers ("Connect Upstox"): the app redirects to Upstox's login dialog,
Upstox redirects back to /api/v1/upstox/callback with a one-time code, and the backend exchanges it
(client secret never leaves the server) and stores the token ENCRYPTED in the provider key store.
If the token is missing/expired, ingestion stops with one clear error instead of thousands.

Endpoints:
  authorize  GET  https://api.upstox.com/v2/login/authorization/dialog?client_id&redirect_uri&response_type=code&state
  token      POST https://api.upstox.com/v2/login/authorization/token   (form: code, client_id, client_secret, redirect_uri, grant_type)
  candles    GET  https://api.upstox.com/v3/historical-candle/{instrument_key}/days/1/{to}/{from}  (≤ 10 years per request)
  chain      GET  https://api.upstox.com/v2/option/chain?instrument_key=NSE_INDEX|Nifty 50&expiry_date=YYYY-MM-DD
  master     GET  https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz  (public, refreshed ~06:00)
Rate limits (standard APIs): 50/s, 500/min, 2000/30 min → requests are paced at ~1/s.

The master lists only CURRENTLY listed stocks: stocks delisted in the past are missing, so some
survivorship bias remains in NSE history (disclosed in the scan's universe summary).
"""
from __future__ import annotations

import gzip
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlencode

import httpx
import pandas as pd

from .base import ChainSnapshot, DataMeta, Instrument, MarketDataProvider, OptionsDataProvider

IST = timezone(timedelta(hours=5, minutes=30))
API = "https://api.upstox.com"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
INDICES = {"NSE_INDEX|Nifty 50": ("NIFTY50", "Nifty 50"), "NSE_INDEX|India VIX": ("INDIAVIX", "India VIX"),
           "NSE_INDEX|Nifty Bank": ("BANKNIFTY", "Nifty Bank")}


class UpstoxAuthError(RuntimeError):
    """Not connected / token expired: the user must reconnect (Admin → Providers → Connect Upstox)."""


class UpstoxError(RuntimeError):
    pass


def next_expiry_ist(now: Optional[datetime] = None) -> datetime:
    """Upstox tokens are valid until 03:30 IST the following day, whenever they were issued."""
    now = (now or datetime.now(IST)).astimezone(IST)
    cut = now.replace(hour=3, minute=30, second=0, microsecond=0)
    return cut if now < cut else cut + timedelta(days=1)


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    return f"{API}/v2/login/authorization/dialog?" + urlencode({"client_id": client_id, "redirect_uri": redirect_uri,
                                                                  "response_type": "code", "state": state})


def verify_token(token: str, http: Optional[httpx.Client] = None) -> None:
    """Cheap read-only check that a token works (last traded price of Nifty 50)."""
    http = http or httpx.Client(timeout=20.0)
    r = http.get(f"{API}/v2/market-quote/ltp", params={"instrument_key": "NSE_INDEX|Nifty 50"},
                 headers={"Accept": "application/json", "Authorization": f"Bearer {token}"})
    if r.status_code == 401:
        raise UpstoxAuthError("Upstox did not accept this token (check you copied the whole Analytics token)")
    if r.status_code != 200:
        raise UpstoxError(f"Upstox check failed with HTTP {r.status_code}")


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str, http: Optional[httpx.Client] = None) -> dict:
    http = http or httpx.Client(timeout=30.0)
    r = http.post(f"{API}/v2/login/authorization/token", headers={"Accept": "application/json"},
                  data={"code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code"})
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get("access_token"):
        err = (body.get("errors") or [{}])[0] if isinstance(body.get("errors"), list) else {}
        raise UpstoxError(f"Upstox token exchange failed: {err.get('message') or body.get('message') or r.status_code}")
    return {"access_token": body["access_token"], "extended_token": body.get("extended_token"), "user_id": body.get("user_id"),
            "expires_at": next_expiry_ist().isoformat()}


class UpstoxClient:
    def __init__(self, token_getter: Callable[[], Optional[dict]], client: Optional[httpx.Client] = None, pause_s: float = 0.95):
        self._token_getter = token_getter  # -> {"access_token", "expires_at"} or None (read fresh: the user may reconnect mid-run)
        self.http = client or httpx.Client(timeout=30.0)
        self.pause_s = pause_s
        self._last = 0.0
        self._master: Optional[list] = None

    def token(self) -> str:
        t = self._token_getter() or {}
        exp = t.get("expires_at")
        if not t.get("access_token") or (exp and datetime.fromisoformat(exp) <= datetime.now(IST)):
            if t.get("kind") == "analytics":
                raise UpstoxAuthError("The Upstox analytics token has expired (1-year validity). Generate a new one on the Upstox "
                                      "Developer Apps page (Analytics tab) and paste it in Admin → Providers.")
            raise UpstoxAuthError("Upstox is not connected. Paste an analytics token (Upstox Developer Apps → Analytics → "
                                  "Generate Token) in Admin → Providers, or click 'Connect Upstox' for a daily login.")
        return t["access_token"]

    def preflight(self) -> None:
        self.token()

    def get(self, path: str, params: Optional[dict] = None, _retry: int = 0) -> dict:
        tok = self.token()
        wait = self.pause_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.http.get(API + path, params=params, headers={"Accept": "application/json", "Authorization": f"Bearer {tok}"})
        self._last = time.monotonic()
        if r.status_code == 401:
            raise UpstoxAuthError("Upstox rejected the token (expired, revoked or regenerated). Update it in Admin → Providers.")
        if r.status_code == 429 and _retry < 4:  # rate limited: back off 2 s, 4 s, 8 s, 16 s
            time.sleep(2 * (2 ** _retry))
            return self.get(path, params, _retry + 1)
        try:
            body = r.json()
        except ValueError:
            raise UpstoxError(f"Upstox returned HTTP {r.status_code} with a non-JSON body") from None
        if r.status_code != 200 or body.get("status") != "success":
            err = (body.get("errors") or [{}])[0] if isinstance(body.get("errors"), list) else {}
            raise UpstoxError(f"Upstox {path.split('/')[2]} failed: {err.get('message') or r.status_code} ({err.get('errorCode', '')})")
        return body

    def master(self) -> list:
        if self._master is None:
            r = self.http.get(MASTER_URL, timeout=120.0)
            r.raise_for_status()
            import json

            self._master = json.loads(gzip.decompress(r.content))
        return self._master


class UpstoxProvider(MarketDataProvider):
    name = "upstox"
    is_sample = False
    lists_full_universe = True  # an instrument missing from the master has left the exchange

    def __init__(self, client: UpstoxClient, history_days: int = 2200):
        self.c = client
        self.history_days = history_days
        self._keys: Dict[str, str] = {}
        self._today: Dict[str, dict] = {}  # symbol -> today's session bar (batch quotes)

    def preflight(self) -> None:
        self.c.preflight()

    def list_instruments(self, market: str = "NSE") -> List[Instrument]:
        out = []
        for x in self.c.master():
            key = x.get("instrument_key", "")
            if key in INDICES:
                sym, name = INDICES[key]
                self._keys[sym] = key
                out.append(Instrument(sym, name, "INDEX", "NSE", "INR", None, is_index=True, extra={"instrument_key": key}))
            elif x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ":
                sym = x["trading_symbol"]
                self._keys[sym] = key
                out.append(Instrument(sym, x.get("name") or sym, "EQUITY", "NSE", "INR", None,
                                      extra={"instrument_key": key, "isin": x.get("isin"), "tick_size": float(x.get("tick_size") or 0) / 100}))
        return out

    def prefetch(self, symbols: List[str]) -> int:
        """Today's session bar for every symbol in ≈ len/500 batch requests (v3 market-quote OHLC, interval 1d).
        The historical-candle API does not include the current day, so without this the 18:30 IST scan would
        always be one session behind. Only after 16:00 IST (session closed); on a holiday the quote's date is the
        previous session and nothing is added. The next ingest re-reads recent history and replaces this
        provisional bar with the official one."""
        now = datetime.now(IST)
        self._today = {}
        if now.time() < datetime.strptime("16:00", "%H:%M").time():
            return 0
        if not self._keys:
            self.list_instruments()
        by_key = {self._keys[s]: s for s in symbols if s in self._keys}
        keys = list(by_key)
        for i in range(0, len(keys), 500):
            data = self.c.get("/v3/market-quote/ohlc", {"instrument_key": ",".join(keys[i:i + 500]), "interval": "1d"}).get("data") or {}
            for q in data.values():
                sym, o = by_key.get(q.get("instrument_token")), q.get("live_ohlc") or {}
                if not sym or not o.get("ts") or o.get("open") in (None, 0):
                    continue
                d = datetime.fromtimestamp(int(o["ts"]) / 1000, IST).date()
                if d == now.date():
                    self._today[sym] = {"ts": pd.Timestamp(d), "open": float(o["open"]), "high": float(o["high"]), "low": float(o["low"]),
                                        "close": float(o["close"]), "volume": float(o.get("volume") or 0)}
        return len(self._today)

    def _key_for(self, symbol: str) -> str:
        if symbol not in self._keys:
            self.list_instruments()
        if symbol not in self._keys:
            raise UpstoxError(f"{symbol} is not in Upstox's instrument master")
        return self._keys[symbol]

    def get_ohlcv(self, symbol: str, interval: str = "1d", start: Optional[date] = None, end: Optional[date] = None):
        if interval != "1d":
            raise NotImplementedError("The Upstox adapter ingests daily candles")
        key = quote(self._key_for(symbol), safe="")
        now = datetime.now(IST)
        last = min(end or now.date(), now.date())
        first = start or (now.date() - timedelta(days=self.history_days))
        rows, b = [], last
        while b >= first:  # ≤ 10 years per request for daily candles
            a = max(first, b - timedelta(days=3650 - 1))
            rows.extend((self.c.get(f"/v3/historical-candle/{key}/days/1/{b:%Y-%m-%d}/{a:%Y-%m-%d}").get("data") or {}).get("candles") or [])
            b = a - timedelta(days=1)
        df = pd.DataFrame([{"ts": pd.Timestamp(str(k[0])[:10]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                            "close": float(k[4]), "volume": float(k[5] or 0)} for k in rows])
        if df.empty:
            df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        else:
            df = df.drop_duplicates("ts").set_index("ts").sort_index()  # Upstox returns newest first
            if now.time() < datetime.strptime("16:00", "%H:%M").time():  # today's candle still forming
                df = df[df.index < pd.Timestamp(now.date())]
        tb = self._today.get(symbol)
        if tb is not None and last >= tb["ts"].date() and tb["ts"] not in df.index:
            today_df = pd.DataFrame([tb]).set_index("ts")
            df = today_df if df.empty else pd.concat([df, today_df]).sort_index()
        return df, DataMeta(source="upstox", is_sample=False, as_of=str(df.index[-1].date()) if len(df) else None,
                            fetched_at=datetime.now(timezone.utc).isoformat())

    def health(self) -> dict:
        try:
            self.c.preflight()
            t = self.c._token_getter() or {}
            return {"provider": self.name, "ok": True, "connected_until": t.get("expires_at")}
        except UpstoxAuthError as exc:
            return {"provider": self.name, "ok": False, "error": str(exc)}


class UpstoxOptionsProvider(OptionsDataProvider):
    """End-of-day NIFTY chain for the nearest `n_expiries` expiries: bid/ask, OI, previous OI and IV from Upstox."""
    name = "upstox"
    is_sample = False

    def __init__(self, client: UpstoxClient, underlying_key: str = "NSE_INDEX|Nifty 50", n_expiries: int = 3, strike_band: float = 0.10):
        self.c = client
        self.underlying_key, self.n_expiries, self.strike_band = underlying_key, n_expiries, strike_band

    def get_option_chain(self, underlying: str, as_of: Optional[date] = None) -> ChainSnapshot:
        today = datetime.now(IST).date()
        if as_of is not None and as_of != today:
            raise UpstoxError("Upstox serves the current chain only; historical chains are not available")
        opts = [x for x in self.c.master() if x.get("segment") == "NSE_FO" and x.get("instrument_type") in ("CE", "PE")
                and x.get("underlying_key") == self.underlying_key]
        exps = sorted({datetime.fromtimestamp(x["expiry"] / 1000, IST).date() for x in opts})
        exps = [e for e in exps if e >= today][: self.n_expiries]
        lot = int(opts[0]["lot_size"]) if opts else 1
        nan = float("nan")
        rows, spot = [], None
        for e in exps:
            data = self.c.get("/v2/option/chain", {"instrument_key": self.underlying_key, "expiry_date": f"{e:%Y-%m-%d}"}).get("data") or []
            for r in data:
                spot = float(r.get("underlying_spot_price") or spot or 0)
                k = float(r["strike_price"])
                if spot and not (spot * (1 - self.strike_band) <= k <= spot * (1 + self.strike_band)):
                    continue
                for side, t in (("call_options", "CE"), ("put_options", "PE")):
                    o = r.get(side) or {}
                    md, g = o.get("market_data") or {}, o.get("option_greeks") or {}
                    if not md:
                        continue
                    pos = lambda v: float(v) if v not in (None, "") and float(v) > 0 else nan  # noqa: E731 — 0 quote = no quote
                    oi, prev = float(md.get("oi") or 0), md.get("prev_oi")
                    rows.append({"expiry": e, "strike": k, "option_type": t, "bid": pos(md.get("bid_price")), "ask": pos(md.get("ask_price")),
                                 "ltp": float(md.get("ltp") or 0), "volume": float(md.get("volume") or 0), "oi": oi,
                                 "oi_change": (oi - float(prev)) if prev is not None else nan,
                                 "iv": (float(g["iv"]) / 100.0) if g.get("iv") not in (None, "", 0) else nan})
        if spot is None:
            raise UpstoxError("Upstox returned an empty option chain")
        meta = DataMeta(source="upstox", is_sample=False, as_of=str(today), fetched_at=datetime.now(timezone.utc).isoformat())
        return ChainSnapshot(underlying, datetime.now(IST), spot, lot, pd.DataFrame(rows), meta)
