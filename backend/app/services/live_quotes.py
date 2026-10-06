"""Live prices for display only (the analysis itself stays end-of-day).

NSE stocks and indices: one Upstox LTP batch request (needs the stored token). CRYPTO: Binance public ticker.
Fetched only while someone has a page open, cached for 20 s per set of symbols. SAMPLE markets get no quotes.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, time
from typing import Dict, List

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cache import get_cache
from app.core.config import get_settings
from app.models import Instrument

log = logging.getLogger(__name__)
TTL_S = 20
MAX_ITEMS = 100


def nse_open(now: datetime) -> bool:
    """Regular NSE session (Mon–Fri 09:15–15:30 IST; exchange holidays are not known here)."""
    return now.weekday() < 5 and time(9, 15) <= now.time() <= time(15, 30)


def _nse(db: Session, symbols: List[str]) -> Dict[str, float]:
    from app.providers.registry import upstox_client

    keys = {}
    for ins in db.scalars(select(Instrument).where(Instrument.market == "NSE", Instrument.symbol.in_(symbols))):
        key = (ins.meta or {}).get("instrument_key")
        if key:
            keys[key] = ins.symbol
    if not keys:
        return {}
    c = upstox_client()
    c.pause_s = 0  # one request per refresh
    data = c.get("/v2/market-quote/ltp", {"instrument_key": ",".join(keys)}).get("data") or {}
    return {keys[q["instrument_token"]]: float(q["last_price"]) for q in data.values()
            if q.get("instrument_token") in keys and q.get("last_price") is not None}


def _crypto(symbols: List[str]) -> Dict[str, float]:
    r = httpx.get(get_settings().binance_spot_url.rstrip("/") + "/api/v3/ticker/price",
                  params={"symbols": json.dumps(symbols, separators=(",", ":"))}, timeout=10, headers={"User-Agent": "MarketEdge/1.0"})
    r.raise_for_status()
    return {t["symbol"]: float(t["price"]) for t in r.json()}


def live_quotes(db: Session, items: List[str]) -> dict:
    """items: ["NSE:GRAPHITE", "NSE:NIFTY50", "CRYPTO:BTCUSDT", …] → {"quotes": {item: {"price", "live"}}, "errors": {market: msg}}."""
    from app.providers.upstox import IST
    from app.services.scan_service import provider_is_sample

    items = sorted({i for i in items if ":" in i})[:MAX_ITEMS]
    key = "live:" + hashlib.sha1(",".join(items).encode()).hexdigest()
    cached = get_cache().get_json(key)
    if cached is not None:
        return cached
    now = datetime.now(IST)
    by_market: Dict[str, List[str]] = {}
    for i in items:
        m, sym = i.split(":", 1)
        by_market.setdefault(m, []).append(sym)
    quotes, errors = {}, {}
    for m, syms in by_market.items():
        if m not in ("NSE", "CRYPTO") or provider_is_sample(m):
            continue
        try:
            prices = _nse(db, syms) if m == "NSE" else _crypto(syms)
        except Exception as exc:  # a quote outage must not break the page
            log.warning("live quotes for %s failed: %s", m, exc)
            errors[m] = "Live prices are unavailable right now" + (" (check the Upstox token in Admin → Providers)" if m == "NSE" else "")
            continue
        live = m == "CRYPTO" or nse_open(now)
        quotes.update({f"{m}:{s}": {"price": p, "live": live} for s, p in prices.items()})
    out = {"quotes": quotes, "errors": errors, "fetched_at": now.isoformat(timespec="seconds"),
           "nse_open": nse_open(now)}
    get_cache().set_json(key, out, ttl=TTL_S)
    return out
