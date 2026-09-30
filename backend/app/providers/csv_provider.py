"""CSV provider for data you have obtained under a valid licence (vendor exports,
broker downloads). Layout:

    {CSV_DATA_DIR}/instruments.csv   symbol,name,asset_class,exchange,currency,sector,lot_size,is_index,listed_on,delisted_on
    {CSV_DATA_DIR}/ohlcv/{SYMBOL}.csv  date,open,high,low,close,volume
    {CSV_DATA_DIR}/fundamentals.csv  symbol,as_of,<fundamental fields...>   (optional)
    {CSV_DATA_DIR}/earnings.csv      symbol,date                            (optional)

The platform does not scrape websites; you are responsible for the licence of files placed here.
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime, timezone
from typing import List, Optional

import pandas as pd

from .base import DataMeta, FundamentalDataProvider, Instrument, MarketDataProvider

_NUMERIC_FUNDAMENTALS = {"revenue_growth_pct", "profit_growth_pct", "eps_growth_pct", "roe_pct", "roce_pct", "debt_to_equity",
                         "operating_margin_pct", "net_margin_pct", "pe", "pb", "peg", "dividend_yield_pct",
                         "promoter_holding_pct", "institutional_holding_pct", "free_cash_flow"}


class CsvMarketDataProvider(MarketDataProvider):
    name = "csv"
    is_sample = False

    def __init__(self, root: str):
        self.root = root

    def _safe_path(self, symbol: str) -> str:
        if not symbol.replace("_", "").replace("-", "").replace("&", "").replace(".", "").isalnum():
            raise ValueError(f"Invalid symbol {symbol!r}")
        return os.path.join(self.root, "ohlcv", f"{symbol}.csv")

    def list_instruments(self, market: str = "NSE") -> List[Instrument]:
        path = os.path.join(self.root, "instruments.csv")
        out = []
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                out.append(Instrument(
                    symbol=r["symbol"], name=r.get("name") or r["symbol"], asset_class=r.get("asset_class") or "EQUITY",
                    exchange=r.get("exchange") or market, currency=r.get("currency") or "INR", sector=r.get("sector") or None,
                    lot_size=int(r.get("lot_size") or 1), is_index=(r.get("is_index", "").lower() in ("1", "true", "yes")),
                    listed_on=r.get("listed_on") or None, delisted_on=r.get("delisted_on") or None,
                ))
        return out

    def get_ohlcv(self, symbol: str, interval: str = "1d", start: Optional[date] = None, end: Optional[date] = None):
        if interval != "1d":
            raise NotImplementedError("CSV provider currently loads daily bars")
        path = self._safe_path(symbol)
        df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).isoformat()
        return df, DataMeta(source=f"csv:{os.path.basename(path)}", is_sample=False, as_of=str(df.index[-1].date()) if len(df) else None, fetched_at=mtime)

    def health(self) -> dict:
        ok = os.path.exists(os.path.join(self.root, "instruments.csv"))
        return {"provider": self.name, "ok": ok, "root": self.root, "error": None if ok else "instruments.csv not found"}


class CsvFundamentalProvider(FundamentalDataProvider):
    name = "csv"

    def __init__(self, root: str):
        self.root = root
        self._fund, self._earn = None, None

    def _load(self):
        if self._fund is None:
            self._fund, self._earn = {}, {}
            fp = os.path.join(self.root, "fundamentals.csv")
            if os.path.exists(fp):
                with open(fp, newline="") as fh:
                    for r in csv.DictReader(fh):
                        rec = {k: float(v) for k, v in r.items() if k in _NUMERIC_FUNDAMENTALS and v not in (None, "")}
                        rec.update({"as_of": r.get("as_of"), "source": "csv:fundamentals.csv"})
                        prev = self._fund.get(r["symbol"])
                        if prev is None or (r.get("as_of") or "") >= (prev.get("as_of") or ""):
                            self._fund[r["symbol"]] = rec
            ep = os.path.join(self.root, "earnings.csv")
            if os.path.exists(ep):
                with open(ep, newline="") as fh:
                    for r in csv.DictReader(fh):
                        self._earn.setdefault(r["symbol"], []).append(date.fromisoformat(r["date"]))

    def get_fundamentals(self, symbol: str) -> Optional[dict]:
        self._load()
        return self._fund.get(symbol)

    def next_earnings_date(self, symbol: str) -> Optional[date]:
        self._load()
        upcoming = sorted(d for d in self._earn.get(symbol, []) if d >= date.today())
        return upcoming[0] if upcoming else None


class CsvOptionsProvider:
    """Option-chain snapshots from licensed files:
        {CSV_DATA_DIR}/options/{UNDERLYING}/{YYYY-MM-DD}.csv
    columns: expiry,strike,option_type,bid,ask,ltp,volume,oi,oi_change[,iv]  (iv in %, optional)
    plus a one-line header file {UNDERLYING}/meta.csv: lot_size,spot_symbol
    The underlying spot is read from the OHLCV file of spot_symbol on the snapshot date."""
    name = "csv"
    is_sample = False

    def __init__(self, root: str, market: CsvMarketDataProvider):
        self.root, self.market = root, market

    def _dir(self, underlying: str) -> str:
        if not underlying.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Invalid underlying {underlying!r}")
        return os.path.join(self.root, "options", underlying)

    def get_option_chain(self, underlying: str, as_of: Optional[date] = None):
        from engine.options.pricing import IST
        from .base import ChainSnapshot

        d = self._dir(underlying)
        files = sorted(f for f in os.listdir(d) if f[:4].isdigit() and f.endswith(".csv"))
        if as_of:
            files = [f for f in files if f[:10] <= str(as_of)]
        if not files:
            raise FileNotFoundError(f"No chain snapshots for {underlying}")
        snap_date = date.fromisoformat(files[-1][:10])
        chain = pd.read_csv(os.path.join(d, files[-1]), parse_dates=["expiry"])
        chain["expiry"] = chain["expiry"].dt.date
        if "iv" in chain.columns:
            chain["iv"] = chain["iv"] / 100
        with open(os.path.join(d, "meta.csv"), newline="") as fh:
            m = next(csv.DictReader(fh))
        bars, _ = self.market.get_ohlcv(m["spot_symbol"], end=snap_date)
        as_of_dt = datetime.combine(snap_date, datetime.min.time().replace(hour=15, minute=30), tzinfo=IST)
        meta = DataMeta(source=f"csv:options/{underlying}/{files[-1]}", is_sample=False, as_of=as_of_dt.isoformat(),
                        fetched_at=datetime.fromtimestamp(os.path.getmtime(os.path.join(d, files[-1])), tz=timezone.utc).isoformat())
        return ChainSnapshot(underlying, as_of_dt, float(bars["close"].iloc[-1]), int(m["lot_size"]), chain, meta)

    def get_iv_history(self, underlying: str):
        return None

    def health(self) -> dict:
        return {"provider": self.name, "ok": os.path.isdir(os.path.join(self.root, "options"))}


class CsvCalendarProvider:
    """Licensed calendar files:
        {CSV_DATA_DIR}/calendar/earnings.csv  symbol,event_date,period,time,eps_estimate,eps_actual,revenue_estimate,revenue_actual,guidance
        {CSV_DATA_DIR}/calendar/economic.csv  event_time,country,currency,name,category,impact,actual,forecast,previous,unit
    """
    name = "csv"

    def __init__(self, root: str):
        self.root = root

    def _rows(self, name: str):
        p = os.path.join(self.root, "calendar", name)
        if not os.path.exists(p):
            return []
        with open(p, newline="") as fh:
            return list(csv.DictReader(fh))

    @staticmethod
    def _num(v):
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None

    def get_earnings(self, symbols, start, end):
        want = set(symbols)
        return [{"symbol": r["symbol"], "event_date": r["event_date"], "period": r.get("period"), "time": r.get("time") or None,
                 "eps_estimate": self._num(r.get("eps_estimate")), "eps_actual": self._num(r.get("eps_actual")),
                 "revenue_estimate": self._num(r.get("revenue_estimate")), "revenue_actual": self._num(r.get("revenue_actual")),
                 "guidance": r.get("guidance") or None, "source": "csv", "is_sample": False}
                for r in self._rows("earnings.csv") if r["symbol"] in want and str(start) <= r["event_date"] <= str(end)]

    def get_events(self, start, end):
        out = []
        for r in self._rows("economic.csv"):
            if str(start) <= r["event_time"][:10] <= str(end):
                out.append({"external_id": f"{r['country']}-{r['name']}-{r['event_time']}", "event_time": r["event_time"], "country": r["country"].upper(),
                            "currency": r.get("currency") or None, "name": r["name"], "category": r.get("category") or None,
                            "impact": r.get("impact") or "Low", "actual": self._num(r.get("actual")), "forecast": self._num(r.get("forecast")),
                            "previous": self._num(r.get("previous")), "unit": r.get("unit") or None, "source": "csv", "is_sample": False})
        return out
