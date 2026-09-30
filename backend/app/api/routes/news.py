from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.db import get_db
from app.models import NewsArticle
from app.services.events_service import earnings_for, economic_events, news_for

router = APIRouter(tags=["news & calendars"])
NOTE = "Sentiment is a lexicon-based classification of the headline and summary (terms shown). It is context only and never generates a setup."


@router.get("/news", dependencies=[Depends(require("market:read"))])
def list_news(db: Session = Depends(get_db), symbol: Optional[str] = Query(None, max_length=64),
              market: Optional[str] = Query(None, pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$"),
              sentiment: Optional[str] = Query(None, pattern="^(Positive|Neutral|Negative)$"),
              category: Optional[str] = Query(None, pattern="^(earnings|regulatory|insider|analyst|macro|geopolitical|announcement|general)$"),
              days: int = Query(14, ge=1, le=90), limit: int = Query(50, ge=1, le=200)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    items = news_for(db, symbols=[symbol.upper()] if symbol else None, market=market, since=since, sentiment=sentiment, category=category, limit=limit)
    return {"items": items, "note": NOTE}


@router.get("/news/{article_id}", dependencies=[Depends(require("market:read"))])
def get_news(article_id: int, db: Session = Depends(get_db)):
    a = db.get(NewsArticle, article_id)
    if a is None:
        raise HTTPException(404, "Article not found")
    return {"id": a.id, "title": a.title, "summary": a.summary, "url": a.url, "source": a.source, "published_at": a.published_at.isoformat(),
            "symbols": [s.symbol for s in a.symbols], "sentiment_label": a.sentiment_label, "sentiment_score": a.sentiment_score,
            "sentiment_terms": a.sentiment_terms, "sentiment_method": a.sentiment_method, "category": a.category, "is_sample": a.is_sample, "note": NOTE}


@router.get("/calendar/economic", dependencies=[Depends(require("market:read"))])
def calendar_economic(db: Session = Depends(get_db), start: Optional[date] = None, end: Optional[date] = None,
                      country: Optional[str] = Query(None, pattern="^[A-Za-z]{2}$"), impact: Optional[str] = Query(None, pattern="^(Low|Medium|High)$")):
    s = datetime.combine(start or date.today() - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)
    e = datetime.combine(end or date.today() + timedelta(days=21), datetime.max.time(), tzinfo=timezone.utc)
    if e < s or (e - s).days > 370:
        raise HTTPException(422, "Invalid range (max 370 days)")
    items = economic_events(db, s, e, country, impact)
    return {"items": items, "start": str(s.date()), "end": str(e.date()),
            "impact_note": "High-impact releases within 24h block forex/index/option setups and warn on others; within 3 days they warn."}


@router.get("/calendar/earnings", dependencies=[Depends(require("market:read"))])
def calendar_earnings(db: Session = Depends(get_db), market: str = Query("NSE", pattern="^(NSE|US|EUROPE|ASIA)$"),
                      start: Optional[date] = None, end: Optional[date] = None, symbol: Optional[str] = Query(None, max_length=64)):
    from sqlalchemy import select

    from app.models import Instrument

    q = select(Instrument).where(Instrument.market == market, Instrument.is_index.is_(False))
    if symbol:
        q = q.where(Instrument.symbol == symbol.upper())
    inst = {i.id: i for i in db.scalars(q)}
    ids = {k: v.symbol for k, v in inst.items()}
    by_sym = {v.symbol: v for v in inst.values()}
    s, e = start or date.today() - timedelta(days=30), end or date.today() + timedelta(days=45)
    items = earnings_for(db, ids, s, e)
    today = date.today()
    for it in items:
        i = by_sym[it["symbol"]]
        it["name"], it["currency"], it["exchange"] = i.name, i.currency, i.exchange
        it["days_until"] = (date.fromisoformat(it["event_date"]) - today).days
        it["warning"] = f"⚠ Earnings in {it['days_until']} days — high event risk" if 0 <= it["days_until"] <= 10 else None
    return {"market": market, "start": str(s), "end": str(e), "units": "EPS per share and revenue in the instrument's currency (as reported by the provider)", "upcoming": [i for i in items if i["days_until"] >= 0],
            "recent": [i for i in items if i["days_until"] < 0][::-1]}
