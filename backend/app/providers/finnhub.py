"""Finnhub adapter (licensed; API key required) for company news, earnings calendar and
economic calendar. Endpoints: /company-news, /news?category=general, /calendar/earnings,
/calendar/economic (economic calendar requires a paid plan — a 401/403 raises
ProviderUnavailable so the platform shows "unavailable" instead of guessing).
Symbol mapping: instrument meta `news_symbol` (e.g. "RELIANCE.NS") else the platform symbol.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import httpx

from .base import EarningsCalendarProvider, EconomicCalendarProvider, NewsProvider

BASE = "https://finnhub.io/api/v1"
IMPACT = {"high": "High", "medium": "Medium", "low": "Low", "3": "High", "2": "Medium", "1": "Low"}


class ProviderUnavailable(RuntimeError):
    pass


class FinnhubProvider(NewsProvider, EconomicCalendarProvider, EarningsCalendarProvider):
    name = "finnhub"

    def __init__(self, api_key: Optional[str], symbol_map: Optional[Dict[str, str]] = None, client: Optional[httpx.Client] = None,
                 rate_per_min: int = 50, base_url: str = BASE):
        self.api_key = api_key
        self.map = symbol_map or {}
        self.http = client or httpx.Client(timeout=20.0)
        self.min_interval = 60.0 / max(rate_per_min, 1)
        self._last = 0.0
        self.base = base_url.rstrip("/")

    def _get(self, path: str, **params):
        if not self.api_key:
            raise ProviderUnavailable("Finnhub API key not configured (admin → Providers → finnhub/API_KEY)")
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.http.get(self.base + path, params={**params, "token": self.api_key})
        if r.status_code in (401, 403):
            raise ProviderUnavailable(f"Finnhub {path} not available on this plan ({r.status_code})")
        r.raise_for_status()
        return r.json()

    def get_news(self, symbols: List[str], since: datetime, market: str) -> List[dict]:
        out, today = [], datetime.now(timezone.utc).date()
        for sym in symbols:
            vendor = self.map.get(sym, sym)
            for a in self._get("/company-news", symbol=vendor, **{"from": str(since.date()), "to": str(today)}) or []:
                out.append(self._article(a, [sym]))
        for a in self._get("/news", category="general") or []:
            out.append({**self._article(a, []), "markets": [market]})
        return out

    @staticmethod
    def _article(a: dict, symbols: List[str]) -> dict:
        return {"external_id": str(a.get("id") or a.get("url")), "published_at": datetime.fromtimestamp(int(a["datetime"]), tz=timezone.utc).isoformat(),
                "title": a.get("headline") or "", "summary": a.get("summary") or "", "url": a.get("url"), "source": a.get("source") or "Finnhub",
                "symbols": symbols, "is_sample": False}

    def get_earnings(self, symbols: List[str], start: date, end: date) -> List[dict]:
        rev = {v: k for k, v in self.map.items()}
        body = self._get("/calendar/earnings", **{"from": str(start), "to": str(end)})
        wanted = set(symbols)
        out = []
        for e in (body or {}).get("earningsCalendar", []):
            sym = rev.get(e.get("symbol"), e.get("symbol"))
            if sym not in wanted:
                continue
            out.append({"symbol": sym, "event_date": e["date"], "period": f"Q{e.get('quarter')} {e.get('year')}", "time": e.get("hour") or None,
                        "eps_estimate": e.get("epsEstimate"), "eps_actual": e.get("epsActual"), "revenue_estimate": e.get("revenueEstimate"),
                        "revenue_actual": e.get("revenueActual"), "guidance": None, "source": "finnhub", "is_sample": False})
        return out

    def get_events(self, start: date, end: date) -> List[dict]:
        body = self._get("/calendar/economic", **{"from": str(start), "to": str(end)})
        out = []
        for e in (body or {}).get("economicCalendar", []):
            t = e.get("time")
            ts = datetime.fromisoformat(t.replace(" ", "T")).replace(tzinfo=timezone.utc) if t else None
            if ts is None:
                continue
            out.append({"external_id": f"{e.get('country')}-{e.get('event')}-{t}", "event_time": ts.isoformat(), "country": (e.get("country") or "").upper(),
                        "currency": None, "name": e.get("event") or "", "category": None, "impact": IMPACT.get(str(e.get("impact", "")).lower(), "Low"),
                        "actual": e.get("actual"), "forecast": e.get("estimate"), "previous": e.get("prev"), "unit": e.get("unit"),
                        "source": "finnhub", "is_sample": False})
        return out
