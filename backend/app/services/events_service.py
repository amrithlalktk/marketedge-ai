"""News + calendars: ingestion (provider → DB, with sentiment) and the queries that
feed setups, the stock page and the AI analyst."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EarningsEvent, EconomicEvent, Instrument, NewsArticle, NewsSymbol
from app.providers.registry import calendar_provider, news_provider
from engine.sentiment import classify

log = logging.getLogger(__name__)


def _ins(db: Session, market: str) -> Dict[str, Instrument]:
    return {i.symbol: i for i in db.scalars(select(Instrument).where(Instrument.market == market, Instrument.is_active.is_(True),
                                                                        Instrument.is_index.is_(False)))}


def _aware(ts) -> datetime:
    t = pd.Timestamp(ts)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


def _sample_only(prov, ins: Dict[str, Instrument]) -> Dict[str, Instrument]:
    """Synthetic news/earnings are generated for SAMPLE instruments only — never about a real instrument."""
    return {k: v for k, v in ins.items() if v.is_sample} if getattr(prov, "is_sample", False) else ins


def purge_sample_news_for_live(db: Session, market: str) -> int:
    """Remove synthetic articles tagged to `market` or to its real instruments (left over from before the switch)."""
    real = {k for k, v in _ins(db, market).items() if not v.is_sample}
    n = 0
    for art in db.scalars(select(NewsArticle).where(NewsArticle.is_sample.is_(True))):
        syms = {x.symbol for x in art.symbols}
        if (syms & real) or (not syms and market in (art.markets or []) and not any(v.is_sample for v in _ins(db, market).values())):
            db.delete(art)
            n += 1
    db.commit()
    return n


def ingest_news(db: Session, market: str, days: int = 14) -> dict:
    prov = news_provider()
    if prov is None:
        return {"skipped": "no news provider configured"}
    ins = _sample_only(prov, _ins(db, market))
    if getattr(prov, "is_sample", False):
        purged = purge_sample_news_for_live(db, market)
        if not ins:
            return {"market": market, "skipped": "market is on real data; sample news is not generated for real instruments", "purged": purged}
    since = datetime.now(timezone.utc) - timedelta(days=days)
    items = prov.get_news(list(ins), since, market)
    existing = {e for (e,) in db.execute(select(NewsArticle.external_id).where(NewsArticle.provider == prov.name))}
    added = 0
    for it in items:
        if it["external_id"] in existing:
            continue
        s = classify(it["title"], it.get("summary", ""))
        art = NewsArticle(provider=prov.name, external_id=it["external_id"][:255], published_at=_aware(it["published_at"]),
                          title=it["title"][:500], summary=(it.get("summary") or "")[:4000], url=it.get("url"), source=(it.get("source") or "")[:128],
                          markets=it.get("markets") or [market], sentiment_label=s["label"], sentiment_score=s["score"],
                          sentiment_method=s["method"], sentiment_terms=s["terms"], category=s["category"], is_sample=bool(it.get("is_sample")))
        art.symbols = [NewsSymbol(symbol=sym) for sym in dict.fromkeys(it.get("symbols") or []) if sym in ins]
        db.add(art)
        existing.add(it["external_id"])
        added += 1
    db.commit()
    return {"market": market, "fetched": len(items), "added": added, "provider": prov.name}


def ingest_calendar(db: Session, markets: List[str], back_days: int = 400, ahead_days: int = 90) -> dict:
    prov = calendar_provider()
    if prov is None:
        return {"skipped": "no calendar provider configured"}
    start, end = date.today() - timedelta(days=back_days), date.today() + timedelta(days=ahead_days)
    stats = {"provider": prov.name, "economic": 0, "earnings": 0, "errors": {}}
    try:
        seen = {e.external_id: e for e in db.scalars(select(EconomicEvent).where(EconomicEvent.provider == prov.name))}
        for e in prov.get_events(start, end):
            row = seen.get(e["external_id"]) or EconomicEvent(provider=prov.name, external_id=e["external_id"][:255])
            row.event_time, row.country, row.currency = _aware(e["event_time"]), (e.get("country") or "")[:8], e.get("currency")
            row.name, row.category, row.impact = e["name"][:255], e.get("category"), e.get("impact") or "Low"
            row.actual, row.forecast, row.previous, row.unit = e.get("actual"), e.get("forecast"), e.get("previous"), e.get("unit")
            row.source, row.is_sample = (e.get("source") or prov.name)[:32], bool(e.get("is_sample"))
            if row.id is None:
                db.add(row)
                seen[row.external_id] = row
            stats["economic"] += 1
        db.commit()
    except Exception as exc:  # e.g. economic calendar not on the vendor plan
        db.rollback()
        stats["errors"]["economic"] = str(exc)[:300]
    for m in markets:
        ins = _sample_only(prov, _ins(db, m))
        if not ins or m in ("FX",):
            continue
        try:
            existing = {(r.instrument_id, r.event_date): r for r in db.scalars(select(EarningsEvent).where(EarningsEvent.instrument_id.in_([i.id for i in ins.values()])))}
            for e in prov.get_earnings(list(ins), start, end):
                i = ins.get(e["symbol"])
                if i is None:
                    continue
                d = date.fromisoformat(str(e["event_date"])[:10])
                row = existing.get((i.id, d)) or EarningsEvent(instrument_id=i.id, event_date=d)
                for k in ("period", "time", "eps_estimate", "eps_actual", "revenue_estimate", "revenue_actual", "guidance"):
                    setattr(row, k, e.get(k))
                row.source, row.is_sample = (e.get("source") or prov.name)[:32], bool(e.get("is_sample"))
                if row.id is None:
                    db.add(row)
                    existing[(i.id, d)] = row
                stats["earnings"] += 1
            db.commit()
        except Exception as exc:
            db.rollback()
            stats["errors"][m] = str(exc)[:300]
    return stats


# ------------------------------------------------------------------ queries
def economic_events(db: Session, start: datetime, end: datetime, country: Optional[str] = None, impact: Optional[str] = None) -> List[dict]:
    q = select(EconomicEvent).where(EconomicEvent.event_time >= start, EconomicEvent.event_time <= end).order_by(EconomicEvent.event_time)
    if country:
        q = q.where(EconomicEvent.country == country.upper())
    if impact:
        q = q.where(EconomicEvent.impact == impact)
    return [{"id": e.id, "event_time": _aware(e.event_time).isoformat(), "country": e.country, "currency": e.currency, "name": e.name, "category": e.category,
             "impact": e.impact, "actual": e.actual, "forecast": e.forecast, "previous": e.previous, "unit": e.unit, "source": e.source,
             "is_sample": e.is_sample, "surprise": (round(e.actual - e.forecast, 4) if e.actual is not None and e.forecast is not None else None)}
            for e in db.scalars(q)]


def _earn_dict(r: EarningsEvent, symbol: str) -> dict:
    sur = lambda a, e: round(100 * (a - e) / abs(e), 2) if a is not None and e not in (None, 0) else None
    return {"symbol": symbol, "event_date": str(r.event_date), "period": r.period, "time": r.time, "eps_estimate": r.eps_estimate, "eps_actual": r.eps_actual,
            "eps_surprise_pct": sur(r.eps_actual, r.eps_estimate), "revenue_estimate": r.revenue_estimate, "revenue_actual": r.revenue_actual,
            "revenue_surprise_pct": sur(r.revenue_actual, r.revenue_estimate), "guidance": r.guidance, "source": r.source, "is_sample": r.is_sample,
            "reported": r.eps_actual is not None or r.revenue_actual is not None}


def earnings_for(db: Session, ids: Dict[int, str], start: date, end: date) -> List[dict]:
    if not ids:
        return []
    q = select(EarningsEvent).where(EarningsEvent.instrument_id.in_(list(ids)), EarningsEvent.event_date >= start,
                                    EarningsEvent.event_date <= end).order_by(EarningsEvent.event_date)
    return [_earn_dict(r, ids[r.instrument_id]) for r in db.scalars(q)]


def next_earnings_days(db: Session, ids: Dict[int, str], today: date) -> Dict[str, int]:
    out = {}
    for e in earnings_for(db, ids, today, today + timedelta(days=120)):
        out.setdefault(e["symbol"], (date.fromisoformat(e["event_date"]) - today).days)
    return out


def news_for(db: Session, symbols: Optional[List[str]] = None, market: Optional[str] = None, since: Optional[datetime] = None,
             sentiment: Optional[str] = None, category: Optional[str] = None, limit: int = 50) -> List[dict]:
    q = select(NewsArticle).order_by(NewsArticle.published_at.desc()).limit(limit)
    if symbols:
        q = q.join(NewsSymbol, NewsSymbol.article_id == NewsArticle.id).where(NewsSymbol.symbol.in_(symbols))
    if since:
        q = q.where(NewsArticle.published_at >= since)
    if sentiment:
        q = q.where(NewsArticle.sentiment_label == sentiment)
    if category:
        q = q.where(NewsArticle.category == category)
    rows = list(db.scalars(q))
    if market and not symbols:
        rows = [r for r in rows if market in (r.markets or [])]
    return [{"id": a.id, "title": a.title, "summary": a.summary, "url": a.url, "source": a.source, "published_at": _aware(a.published_at).isoformat(),
             "symbols": [s.symbol for s in a.symbols], "markets": a.markets, "sentiment_label": a.sentiment_label, "sentiment_score": a.sentiment_score,
             "sentiment_method": a.sentiment_method, "sentiment_terms": a.sentiment_terms, "category": a.category, "is_sample": a.is_sample,
             "provider": a.provider} for a in rows]


def news_by_symbol(db: Session, symbols: List[str], days: int = 7) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    if not symbols:
        return out
    since = datetime.now(timezone.utc) - timedelta(days=days)
    for a in news_for(db, symbols=symbols, since=since, limit=5000):
        for s in a["symbols"]:
            if s in symbols:
                out.setdefault(s, []).append(a)
    return out
