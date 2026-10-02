"""Angel One SmartAPI adapter: NSE equities + indices (daily candles) and the NIFTY option chain.

Uses the account holder's own SmartAPI credentials (free with an Angel One account; personal,
non-redistributed use). Credentials live encrypted in the provider key store (Admin → Providers,
provider "angelone"): API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET. They are never logged or returned.

Endpoints (from Angel One's official SDK, github.com/angel-one/smartapi-python):
  login    POST /rest/auth/angelbroking/user/v1/loginByPassword   {clientcode, password(MPIN), totp}
  candles  POST /rest/secure/angelbroking/historical/v1/getCandleData  {exchange, symboltoken, interval, fromdate, todate}
  quotes   POST /rest/secure/angelbroking/market/v1/quote          {mode: "FULL", exchangeTokens: {"NFO": [...]}}  (≤50 tokens)
  master   GET  margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json (public)

Limits: candles ≈3 req/s and 500/min (forum reports false positives below that), so requests
are paced (`pause_s`) and a rate-limit answer is retried with back-off. Daily history is fetched
in `chunk_days` windows (the per-request maximum for ONE_DAY is not documented; 1000 is conservative).

Universe: every NSE "-EQ" series stock in the master (point-in-time top-N by traded value is decided
per date by engine.universe). The master lists only CURRENTLY listed stocks, so stocks delisted in the
past are missing — a residual survivorship bias that is disclosed in the scan's universe summary.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
import pandas as pd

from .base import ChainSnapshot, DataMeta, Instrument, MarketDataProvider, OptionsDataProvider

IST = timezone(timedelta(hours=5, minutes=30))
INDICES = {"99926000": ("NIFTY50", "Nifty 50"), "99926017": ("INDIAVIX", "India VIX"), "99926009": ("BANKNIFTY", "Nifty Bank")}


class AngelOneError(RuntimeError):
    pass


class AngelOneSession:
    ROOT = "https://apiconnect.angelone.in"
    MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

    def __init__(self, api_key: Optional[str], client_code: Optional[str], mpin: Optional[str], totp_secret: Optional[str],
                 client: Optional[httpx.Client] = None, pause_s: float = 0.4, token_cache=None):
        self.api_key, self.client_code, self.mpin, self.totp_secret = api_key, client_code, mpin, totp_secret
        self.http = client or httpx.Client(timeout=30.0)
        self.pause_s = pause_s
        self._jwt: Optional[str] = None
        self._cache = token_cache  # app cache (Redis) so every process does not log in separately
        self._master: Optional[list] = None
        self._last = 0.0

    @property
    def configured(self) -> bool:
        return all([self.api_key, self.client_code, self.mpin, self.totp_secret])

    def _headers(self, auth: bool) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json", "X-UserType": "USER", "X-SourceID": "WEB",
             "X-ClientLocalIP": "127.0.0.1", "X-ClientPublicIP": "127.0.0.1", "X-MACAddress": "00:00:00:00:00:00",
             "X-PrivateKey": self.api_key or ""}
        if auth:
            h["Authorization"] = f"Bearer {self._token()}"
        return h

    def _cache_key(self) -> str:
        return f"angelone:jwt:{self.client_code}"

    def _token(self) -> str:
        if self._jwt:
            return self._jwt
        if self._cache is not None:
            cached = self._cache.get_json(self._cache_key())
            if cached:
                self._jwt = cached
                return cached
        return self.login()

    def login(self) -> str:
        if not self.configured:
            raise AngelOneError("Angel One credentials are not configured (Admin → Providers: angelone API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET)")
        import pyotp

        r = self.http.post(self.ROOT + "/rest/auth/angelbroking/user/v1/loginByPassword", headers=self._headers(auth=False),
                           json={"clientcode": self.client_code, "password": self.mpin, "totp": pyotp.TOTP(self.totp_secret).now()})
        body = self._json(r)
        if not body.get("status") or not (body.get("data") or {}).get("jwtToken"):
            raise AngelOneError(f"Angel One login failed: {body.get('message') or r.status_code} ({body.get('errorcode', '')})")
        self._jwt = body["data"]["jwtToken"]
        if self._cache is not None:
            self._cache.set_json(self._cache_key(), self._jwt, ttl=6 * 3600)  # session tokens are valid for the trading day
        return self._jwt

    @staticmethod
    def _json(r: httpx.Response) -> dict:
        try:
            return r.json()
        except ValueError:
            raise AngelOneError(f"Angel One returned HTTP {r.status_code} with a non-JSON body") from None

    def post(self, path: str, body: dict, _retry: int = 0, _relogged: bool = False) -> dict:
        wait = self.pause_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.http.post(self.ROOT + path, headers=self._headers(auth=True), json=body)
        self._last = time.monotonic()
        data = self._json(r)
        msg = str(data.get("message") or "")
        if r.status_code == 403 or data.get("errorcode") in ("AG8001", "AG8002", "AG8003") or "Invalid Token" in msg:
            if not _relogged:  # expired session: log in again once
                self._jwt = None
                if self._cache is not None:
                    self._cache.set_json(self._cache_key(), None, ttl=1)
                self.login()
                return self.post(path, body, _retry, _relogged=True)
        if r.status_code == 429 or "access rate" in msg.lower() or data.get("errorcode") == "AB1004":
            if _retry < 4:  # rate limited: back off 1.5 s, 3 s, 6 s, 12 s
                time.sleep(1.5 * (2 ** _retry))
                return self.post(path, body, _retry + 1, _relogged)
        if not data.get("status"):
            raise AngelOneError(f"Angel One {path.rsplit('/', 1)[-1]} failed: {msg or r.status_code} ({data.get('errorcode', '')})")
        return data

    def master(self) -> list:
        if self._master is None:
            r = self.http.get(self.MASTER_URL, timeout=120.0)
            r.raise_for_status()
            self._master = r.json()
        return self._master


class AngelOneProvider(MarketDataProvider):
    name = "angelone"
    is_sample = False
    lists_full_universe = True  # an instrument missing from the master has left the exchange

    def __init__(self, session: AngelOneSession, history_days: int = 2200, chunk_days: int = 1000):
        self.s = session
        self.history_days, self.chunk_days = history_days, chunk_days
        self._tokens: Dict[str, str] = {}

    def list_instruments(self, market: str = "NSE") -> List[Instrument]:
        out = []
        for x in self.s.master():
            if x.get("exch_seg") != "NSE":
                continue
            if x.get("instrumenttype") == "AMXIDX" and x["token"] in INDICES:
                sym, name = INDICES[x["token"]]
                self._tokens[sym] = x["token"]
                out.append(Instrument(sym, name, "INDEX", "NSE", "INR", None, is_index=True, extra={"token": x["token"]}))
            elif x.get("symbol", "").endswith("-EQ") and not x.get("instrumenttype"):
                sym = x["name"]
                self._tokens[sym] = x["token"]
                out.append(Instrument(sym, sym, "EQUITY", "NSE", "INR", None,
                                      extra={"token": x["token"], "angel_symbol": x["symbol"], "tick_size": float(x.get("tick_size") or 0) / 100}))
        return out

    def _token_for(self, symbol: str) -> str:
        if symbol not in self._tokens:
            self.list_instruments()
        if symbol not in self._tokens:
            raise AngelOneError(f"{symbol} is not in Angel One's instrument master")
        return self._tokens[symbol]

    def get_ohlcv(self, symbol: str, interval: str = "1d", start: Optional[date] = None, end: Optional[date] = None):
        if interval != "1d":
            raise NotImplementedError("The Angel One adapter ingests daily candles")
        token = self._token_for(symbol)
        now = datetime.now(IST)
        last = min(end or now.date(), now.date())
        first = start or (now.date() - timedelta(days=self.history_days))
        rows = []
        a = first
        while a <= last:
            b = min(a + timedelta(days=self.chunk_days - 1), last)
            data = self.s.post("/rest/secure/angelbroking/historical/v1/getCandleData",
                               {"exchange": "NSE", "symboltoken": token, "interval": "ONE_DAY",
                                "fromdate": f"{a:%Y-%m-%d} 09:15", "todate": f"{b:%Y-%m-%d} 15:30"}).get("data") or []
            rows.extend(data)
            a = b + timedelta(days=1)
        df = pd.DataFrame([{"ts": pd.Timestamp(str(k[0])[:10]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                            "close": float(k[4]), "volume": float(k[5] or 0)} for k in rows])
        if df.empty:
            df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        else:
            df = df.drop_duplicates("ts").set_index("ts").sort_index()
            # today's candle is still forming until the close (+ a margin for the final print)
            if now.time() < datetime.strptime("16:00", "%H:%M").time():
                df = df[df.index < pd.Timestamp(now.date())]
        meta = DataMeta(source="angelone", is_sample=False, as_of=str(df.index[-1].date()) if len(df) else None,
                        fetched_at=datetime.now(timezone.utc).isoformat())
        return df, meta

    def health(self) -> dict:
        if not self.s.configured:
            return {"provider": self.name, "ok": False, "error": "credentials not configured"}
        try:
            self.s.login()
            return {"provider": self.name, "ok": True}
        except Exception as exc:
            return {"provider": self.name, "ok": False, "error": str(exc)[:200]}


class AngelOneOptionsProvider(OptionsDataProvider):
    """End-of-day NIFTY chain: nearest `n_expiries` expiries, strikes within ±`strike_band` of spot."""
    name = "angelone"
    is_sample = False

    def __init__(self, session: AngelOneSession, underlying_name: str = "NIFTY", spot_instrument: str = "99926000",
                 n_expiries: int = 3, strike_band: float = 0.10):
        self.s = session
        self.underlying_name, self.spot_instrument = underlying_name, spot_instrument  # Angel instrument number of the index
        self.n_expiries, self.strike_band = n_expiries, strike_band

    def _quotes(self, exchange: str, tokens: List[str]) -> List[dict]:
        out = []
        for i in range(0, len(tokens), 50):
            d = self.s.post("/rest/secure/angelbroking/market/v1/quote", {"mode": "FULL", "exchangeTokens": {exchange: tokens[i:i + 50]}})
            out.extend((d.get("data") or {}).get("fetched") or [])
        return out

    def get_option_chain(self, underlying: str, as_of: Optional[date] = None) -> ChainSnapshot:
        if as_of is not None and as_of != datetime.now(IST).date():
            raise AngelOneError("Angel One serves the live chain only; historical chains are not available")
        spot_q = self._quotes("NSE", [self.spot_instrument])
        if not spot_q:
            raise AngelOneError("No spot quote for the underlying")
        spot = float(spot_q[0]["ltp"])
        today = datetime.now(IST).date()
        opts = [x for x in self.s.master() if x.get("exch_seg") == "NFO" and x.get("name") == self.underlying_name
                and x.get("instrumenttype") == "OPTIDX"]
        for x in opts:
            x["_exp"] = datetime.strptime(x["expiry"], "%d%b%Y").date()
            x["_k"] = float(x["strike"]) / 100.0
        expiries = sorted({x["_exp"] for x in opts if x["_exp"] >= today})[: self.n_expiries]
        lo, hi = spot * (1 - self.strike_band), spot * (1 + self.strike_band)
        pick = [x for x in opts if x["_exp"] in expiries and lo <= x["_k"] <= hi]
        by_token = {x["token"]: x for x in pick}
        rows = []
        for q in self._quotes("NFO", list(by_token)):
            x = by_token.get(str(q.get("symbolToken")))
            if x is None:
                continue
            depth = q.get("depth") or {}
            bid = next((float(d["price"]) for d in depth.get("buy") or [] if float(d.get("price") or 0) > 0), None)
            ask = next((float(d["price"]) for d in depth.get("sell") or [] if float(d.get("price") or 0) > 0), None)
            rows.append({"expiry": x["_exp"], "strike": x["_k"], "option_type": x["symbol"][-2:],
                         "bid": bid if bid is not None else float("nan"), "ask": ask if ask is not None else float("nan"),
                         "ltp": float(q.get("ltp") or 0), "volume": float(q.get("tradeVolume") or 0), "oi": float(q.get("opnInterest") or 0),
                         "oi_change": float("nan")})
        lot = int(pick[0]["lotsize"]) if pick else 1
        meta = DataMeta(source="angelone", is_sample=False, as_of=str(today), fetched_at=datetime.now(timezone.utc).isoformat(),
                        note="oi_change is computed from the previous stored snapshot")
        return ChainSnapshot(underlying, datetime.now(IST), spot, lot, pd.DataFrame(rows), meta)
