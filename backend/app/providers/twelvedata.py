"""Twelve Data adapter for global equities/ETFs and forex (licensed vendor; API key required).

Universe: {CSV_DATA_DIR}/universe/{MARKET}.csv with columns
    symbol,vendor_symbol,name,asset_class,exchange,currency,sector,is_index[,base,quote]
(`symbol` is the platform id; `vendor_symbol` is what Twelve Data expects, e.g. "AAPL", "EUR/USD", "SAP").
The API key is read from the encrypted admin key store (provider=twelvedata, name=API_KEY)
or the TWELVEDATA_API_KEY environment variable. Requests are throttled to the
plan's per-minute limit. Your Twelve Data plan's terms govern redistribution.
"""
from __future__ import annotations

import csv
import os
import time
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import httpx
import pandas as pd

from .base import DataMeta, Instrument, MarketDataProvider

BASE = "https://api.twelvedata.com"


class TwelveDataProvider(MarketDataProvider):
    name = "twelvedata"
    is_sample = False

    def __init__(self, market: str, api_key: Optional[str], universe_dir: str, rate_per_min: int = 8,
                 client: Optional[httpx.Client] = None, base_url: str = BASE):
        self.market, self.api_key, self.universe_dir = market, api_key, universe_dir
        self.min_interval = 60.0 / max(rate_per_min, 1)
        self.http = client or httpx.Client(timeout=30.0)
        self.base_url = base_url.rstrip("/")
        self._last = 0.0
        self._vendor: Dict[str, dict] = {}

    def _universe_path(self) -> str:
        if not self.market.isalnum():
            raise ValueError("invalid market")
        return os.path.join(self.universe_dir, "universe", f"{self.market}.csv")

    def list_instruments(self, market: str = None) -> List[Instrument]:
        out = []
        with open(self._universe_path(), newline="") as fh:
            for r in csv.DictReader(fh):
                self._vendor[r["symbol"]] = r
                extra = {k: r[k] for k in ("base", "quote") if r.get(k)}
                extra["vendor_symbol"] = r.get("vendor_symbol") or r["symbol"]
                out.append(Instrument(r["symbol"], r.get("name") or r["symbol"], r.get("asset_class") or "EQUITY", r.get("exchange") or self.market,
                                      r.get("currency") or "USD", r.get("sector") or None,
                                      is_index=(r.get("is_index", "").lower() in ("1", "true", "yes")), extra=extra))
        return out

    def _throttle(self):
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get_ohlcv(self, symbol: str, interval: str = "1d", start: Optional[date] = None, end: Optional[date] = None):
        if interval != "1d":
            raise NotImplementedError("Twelve Data adapter ingests daily bars")
        if not self.api_key:
            raise RuntimeError("Twelve Data API key not configured (admin → Providers → add key twelvedata/API_KEY)")
        if not self._vendor:
            self.list_instruments()
        v = self._vendor.get(symbol, {})
        params = {"symbol": v.get("vendor_symbol") or symbol, "interval": "1day", "outputsize": 5000, "order": "ASC", "apikey": self.api_key}
        if v.get("exchange") and v.get("asset_class") not in ("FOREX", "INDEX"):
            params["exchange"] = v["exchange"]
        if start:
            params["start_date"] = str(start)
        if end:
            params["end_date"] = str(end)
        self._throttle()
        r = self.http.get(self.base_url + "/time_series", params=params)
        r.raise_for_status()
        body = r.json()
        if body.get("status") == "error":
            raise RuntimeError(f"Twelve Data error for {symbol}: {body.get('message')}")
        vals = body.get("values") or []
        df = pd.DataFrame([{"ts": pd.Timestamp(x["datetime"]), "open": float(x["open"]), "high": float(x["high"]), "low": float(x["low"]),
                            "close": float(x["close"]), "volume": float(x.get("volume") or 0.0)} for x in vals])
        df = df.set_index("ts").sort_index() if len(df) else pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        today = pd.Timestamp(datetime.now(timezone.utc).date())
        df = df[df.index < today]  # today's bar may still be forming
        return df, DataMeta(source="twelvedata", is_sample=False, as_of=str(df.index[-1].date()) if len(df) else None,
                            fetched_at=datetime.now(timezone.utc).isoformat())

    def health(self) -> dict:
        ok = os.path.exists(self._universe_path()) and bool(self.api_key)
        return {"provider": f"twelvedata:{self.market}", "ok": ok,
                "error": None if ok else "universe file or API key missing"}
