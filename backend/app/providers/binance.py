"""Binance public market-data adapter (crypto spot + USDⓈ-M perpetual context).

Uses only public, unauthenticated market-data endpoints (no account access):
  spot:    {spot_url}/api/v3/exchangeInfo, /api/v3/ticker/24hr, /api/v3/ticker/bookTicker, /api/v3/klines
  futures: {futures_url}/fapi/v1/fundingRate, /futures/data/openInterestHist, /futures/data/globalLongShortAccountRatio
The default spot host is Binance's market-data-only mirror (data-api.binance.vision).
Binance's API terms apply; some jurisdictions are restricted — configure a
permitted host (e.g. an exchange you are allowed to use) via settings.

Daily klines are UTC days. The still-forming current-day kline is DROPPED so
only complete bars are stored (no partial-bar look-ahead).

Point-in-time mode (`pit_universe=True`, the default): list_instruments returns the whole
candidate POOL — every eligible USDT pair trading now plus every DELISTED one found in Binance's
public bulk archive (data.binance.vision) — and the top-N cut is made per date by
engine.universe from stored data. Without the delisted coins the history would only contain
survivors (survivorship bias). Delisted history comes from the archive's monthly zip files.
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
import pandas as pd

from .base import DataMeta, Instrument, MarketDataProvider

STABLE = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "USDD", "PYUSD", "EUR", "EURI", "AEUR", "GBP", "TRY", "BRL", "USD1"}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
COMMODITY_BACKED = {"PAXG", "XAUT", "KAU", "KAG", "DGX"}  # gold/silver tokens: commodity exposure, not crypto risk


def looks_pegged(ticker: dict) -> bool:
    """USD-pegged by behaviour, not by name (new stablecoins appear constantly):
    last price within 10% of 1.0 and a 24h high/low range under 0.5%."""
    try:
        last, hi, lo = float(ticker["lastPrice"]), float(ticker["highPrice"]), float(ticker["lowPrice"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0.9 <= last <= 1.1 and lo > 0 and (hi / lo - 1) < 0.005


class BinanceProvider(MarketDataProvider):
    name = "binance"
    is_sample = False

    ARCHIVE_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
    ARCHIVE_FILES = "https://data.binance.vision"

    def __init__(self, spot_url: str = "https://data-api.binance.vision", futures_url: str = "https://fapi.binance.com",
                 universe_size: int = 40, history_days: int = 2200, client: Optional[httpx.Client] = None, pause_s: float = 0.1,
                 exclude: Optional[List[str]] = None, pit_universe: bool = False, archive_threads: int = 8):
        self.pit_universe, self.archive_threads = pit_universe, archive_threads
        self._delisted: Dict[str, List[str]] = {}   # symbol -> archive month keys
        self._current_top: Optional[set] = None
        self.exclude = {x.upper() for x in (exclude or [])}  # base assets, e.g. ["CRCLB"]
        self.spot_url, self.futures_url = spot_url.rstrip("/"), futures_url.rstrip("/")
        self.universe_size, self.history_days, self.pause_s = universe_size, history_days, pause_s
        self.http = client or httpx.Client(timeout=20.0, headers={"User-Agent": "MarketEdge/1.0"})
        self._spread: Dict[str, float] = {}
        self.rejected: set = set()

    def _get(self, base: str, path: str, **params):
        r = self.http.get(base + path, params=params)
        if r.status_code in (418, 429):  # rate limited: honour Retry-After once
            time.sleep(float(r.headers.get("Retry-After", 5)))
            r = self.http.get(base + path, params=params)
        r.raise_for_status()
        if self.pause_s:
            time.sleep(self.pause_s)
        return r.json()

    def list_instruments(self, market: str = "CRYPTO") -> List[Instrument]:
        info = self._get(self.spot_url, "/api/v3/exchangeInfo", permissions="SPOT")
        usdt = [s for s in info["symbols"] if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"]
        banned = STABLE | COMMODITY_BACKED | self.exclude
        trading = {s["symbol"]: s for s in usdt if s["baseAsset"] not in banned and not s["baseAsset"].endswith(LEVERAGED_SUFFIXES)}
        all_tickers = self._get(self.spot_url, "/api/v3/ticker/24hr")
        pegged = {t["symbol"] for t in all_tickers if t["symbol"] in trading and looks_pegged(t)}
        # symbols deliberately left out, so already-stored ones are deactivated by the ingest
        self.rejected = {s["symbol"] for s in usdt if s["symbol"] not in trading} | pegged
        tickers = [t for t in all_tickers if t["symbol"] in trading and t["symbol"] not in pegged]
        tickers.sort(key=lambda t: -float(t.get("quoteVolume") or 0))
        self._current_top = {t["symbol"] for t in tickers[: self.universe_size]}
        top = tickers if self.pit_universe else tickers[: self.universe_size]
        books = {b["symbol"]: b for b in self._get(self.spot_url, "/api/v3/ticker/bookTicker")}
        out = []
        for t in top:
            sym = t["symbol"]
            b = books.get(sym, {})
            bid, ask = float(b.get("bidPrice") or 0), float(b.get("askPrice") or 0)
            spread_bps = 1e4 * (ask - bid) / ((ask + bid) / 2) if bid > 0 and ask > 0 else None
            self._spread[sym] = spread_bps
            out.append(Instrument(sym, f"{trading[sym]['baseAsset']} / USDT", "CRYPTO", "BINANCE", "USD", None,
                                  extra={"base": trading[sym]["baseAsset"], "quote": "USDT", "spread_bps": spread_bps,
                                         "quote_volume_24h": float(t.get("quoteVolume") or 0), "status": "trading"}))
        if self.pit_universe:
            out.extend(self._delisted_instruments(set(trading) | {s["symbol"] for s in usdt}, banned))
        return out

    # ------------------------------------------------------------------ archive (delisted pairs)
    def _archive_list(self, prefix: str) -> tuple:
        """(common prefixes, keys) under `prefix` in the public S3 listing, all pages."""
        prefixes, keys, marker = [], [], ""
        while True:
            r = self.http.get(self.ARCHIVE_LIST, params={"delimiter": "/", "prefix": prefix, **({"marker": marker} if marker else {})})
            r.raise_for_status()
            t = r.text
            prefixes += re.findall(r"<Prefix>" + re.escape(prefix) + r"([^/<]+)/</Prefix>", t)
            keys += re.findall(r"<Key>([^<]+)</Key>", t)
            m = re.search(r"<NextMarker>([^<]+)</NextMarker>", t)
            if "<IsTruncated>true</IsTruncated>" not in t or not m:
                return prefixes, keys
            marker = m.group(1)

    def _delisted_instruments(self, trading_now: set, banned: set) -> List[Instrument]:
        syms, _ = self._archive_list("data/spot/monthly/klines/")
        cand = [x for x in syms if x.endswith("USDT") and x not in trading_now]
        cand = [x for x in cand if x[:-4] not in banned and not x[:-4].endswith(LEVERAGED_SUFFIXES)]

        def months(sym):
            _, keys = self._archive_list(f"data/spot/monthly/klines/{sym}/1d/")
            return sym, sorted(k for k in keys if k.endswith(".zip"))

        with ThreadPoolExecutor(self.archive_threads) as ex:
            found = dict(ex.map(months, cand))
        out = []
        for sym, ks in sorted(found.items()):
            if not ks:
                continue
            first, last = ks[0][-11:-4], ks[-1][-11:-4]  # YYYY-MM
            y, m = int(last[:4]), int(last[5:])
            month_end = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)).isoformat()
            self._delisted[sym] = ks
            out.append(Instrument(sym, f"{sym[:-4]} / USDT (delisted)", "CRYPTO", "BINANCE", "USD", None,
                                  listed_on=f"{first}-01", delisted_on=month_end,
                                  extra={"base": sym[:-4], "quote": "USDT", "status": "delisted", "source": "binance-archive"}))
        return out

    def _archive_ohlcv(self, symbol: str, start: Optional[date]) -> pd.DataFrame:
        keys = self._delisted[symbol]
        if start is not None:  # delisted history is final: once stored there is nothing new to fetch
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        def fetch(key):
            r = self.http.get(f"{self.ARCHIVE_FILES}/{key}")
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:  # zip CRC check → corrupt downloads raise
                raw = z.read(z.namelist()[0]).decode()
            rows = []
            for line in raw.splitlines():
                p = line.split(",")
                if len(p) < 6 or not p[0].strip().isdigit():
                    continue  # header line (newer files)
                ts = int(p[0])
                ts = ts // 1000 if ts > 10**14 else ts  # archive switched to microseconds in 2025
                rows.append((pd.Timestamp(ts, unit="ms"), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])))
            return rows

        with ThreadPoolExecutor(self.archive_threads) as ex:
            rows = [row for part in ex.map(fetch, keys) for row in part]
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts").set_index("ts").sort_index()
        return df

    @staticmethod
    def _after_last_gap(df: pd.DataFrame, max_gap_days: int = 30) -> pd.DataFrame:
        """Binance re-uses tickers (e.g. LUNA after the 2022 collapse). A gap longer than
        `max_gap_days` means a different asset may follow, so only the latest segment is kept."""
        if len(df) < 2:
            return df
        gaps = df.index.to_series().diff() > pd.Timedelta(days=max_gap_days)
        return df[df.index >= gaps[gaps].index[-1]] if gaps.any() else df

    def get_ohlcv(self, symbol: str, interval: str = "1d", start: Optional[date] = None, end: Optional[date] = None):
        if interval != "1d":
            raise NotImplementedError("Binance adapter ingests daily klines")
        if symbol in self._delisted:
            df = self._after_last_gap(self._archive_ohlcv(symbol, start))
            if end is not None and len(df):
                df = df[df.index <= pd.Timestamp(end)]
            return df, DataMeta(source="binance-archive", is_sample=False, as_of=str(df.index[-1].date()) if len(df) else None,
                                fetched_at=datetime.now(timezone.utc).isoformat(), note="delisted pair: history from data.binance.vision")
        now_ms = int(time.time() * 1000)
        start_ms = int(pd.Timestamp(start or (pd.Timestamp.utcnow().date() - pd.Timedelta(days=self.history_days))).timestamp() * 1000)
        rows = []
        while True:
            chunk = self._get(self.spot_url, "/api/v3/klines", symbol=symbol, interval="1d", startTime=start_ms, limit=1000)
            if not chunk:
                break
            rows.extend(chunk)
            if len(chunk) < 1000:
                break
            start_ms = int(chunk[-1][0]) + 86_400_000
        complete = [k for k in rows if int(k[6]) < now_ms]  # close_time in the past → bar complete
        df = pd.DataFrame([{"ts": pd.Timestamp(int(k[0]), unit="ms"), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                            "close": float(k[4]), "volume": float(k[5])} for k in complete]).set_index("ts") if complete else pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"])
        df = self._after_last_gap(df)
        if end is not None and len(df):
            df = df[df.index <= pd.Timestamp(end)]
        meta = DataMeta(source="binance", is_sample=False, as_of=str(df.index[-1].date()) if len(df) else None,
                        fetched_at=datetime.now(timezone.utc).isoformat())
        return df, meta

    def get_derivatives(self, symbol: str) -> Optional[pd.DataFrame]:
        """Last ~30 days of funding (per 8h, averaged per UTC day), open interest (USD) and long/short account ratio."""
        if symbol in self._delisted or (self._current_top is not None and symbol not in self._current_top):
            return None  # context for today's candidates only; the pool's long tail does not need it
        try:
            fr = self._get(self.futures_url, "/fapi/v1/fundingRate", symbol=symbol, limit=100)
            oi = self._get(self.futures_url, "/futures/data/openInterestHist", symbol=symbol, period="1d", limit=30)
            ls = self._get(self.futures_url, "/futures/data/globalLongShortAccountRatio", symbol=symbol, period="1d", limit=30)
        except httpx.HTTPStatusError:
            return None  # no perpetual listed for this symbol
        f = (pd.DataFrame([(pd.Timestamp(int(x["fundingTime"]), unit="ms").normalize(), float(x["fundingRate"])) for x in fr],
                          columns=["d", "v"]).groupby("d")["v"].mean() if fr else pd.Series(dtype=float))
        o = pd.Series({pd.Timestamp(int(x["timestamp"]), unit="ms").normalize(): float(x["sumOpenInterestValue"]) for x in oi}, dtype=float)
        r = pd.Series({pd.Timestamp(int(x["timestamp"]), unit="ms").normalize(): float(x["longShortRatio"]) for x in ls}, dtype=float)
        df = pd.concat({"funding_rate": f, "open_interest": o, "long_short_ratio": r}, axis=1).sort_index()
        return df if len(df) else None

    def health(self) -> dict:
        try:
            self._get(self.spot_url, "/api/v3/ping")
            return {"provider": self.name, "ok": True, "host": self.spot_url}
        except Exception as exc:
            return {"provider": self.name, "ok": False, "error": str(exc)[:200]}
