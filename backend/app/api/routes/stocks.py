from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.cache import get_cache
from app.core.db import get_db
from app.models import Instrument, MarketRegime, Sector, Signal, SignalOutcome
from app.providers.registry import fundamental_provider
from app.services.market_data import data_meta, load_bars
from app.services.scan_service import _json_safe, latest_events, latest_run
from app.services.settings_service import engine_config
from engine.analyzer import DISCLAIMER, Analyzer, MarketContext
from engine.features import build_features
from engine.levels import price_decimals, price_round
from engine.structure import sr_levels

router = APIRouter(prefix="/stocks", tags=["stocks"])


def _instrument(db: Session, symbol: str) -> Instrument:
    ins = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if ins is None:
        raise HTTPException(404, f"Unknown symbol {symbol}")
    return ins


def _info(i: Instrument) -> dict:
    return {"symbol": i.symbol, "name": i.name, "exchange": i.exchange, "market": i.market, "currency": i.currency, "asset_class": i.asset_class,
            "sector": i.sector.name if i.sector else None, "is_index": i.is_index, "is_sample": i.is_sample, "lot_size": i.lot_size,
            "listed_on": str(i.listed_on) if i.listed_on else None, "delisted_on": str(i.delisted_on) if i.delisted_on else None}


@router.get("", dependencies=[Depends(require("analysis:read"))])
def list_stocks(db: Session = Depends(get_db), q: Optional[str] = Query(None, max_length=64), sector: Optional[str] = None,
                include_indices: bool = False, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                market: Optional[str] = Query(None, pattern="^(NSE|CRYPTO|US|EUROPE|ASIA|FX)$")):
    stmt = select(Instrument).where(Instrument.is_active.is_(True))
    if market:
        stmt = stmt.where(Instrument.market == market)
    if not include_indices:
        stmt = stmt.where(Instrument.is_index.is_(False))
    if q:
        like = f"%{q.upper()}%"
        stmt = stmt.where(or_(func.upper(Instrument.symbol).like(like), func.upper(Instrument.name).like(like)))
    if sector:
        stmt = stmt.join(Sector).where(Sector.name == sector)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Instrument.symbol).offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "items": [_info(r) for r in rows]}


@router.get("/{symbol}", dependencies=[Depends(require("analysis:read"))])
def stock_detail(symbol: str, db: Session = Depends(get_db)):
    ins = _instrument(db, symbol)
    bars = load_bars(db, {ins.id: ins.symbol}).get(ins.symbol)
    quote = None
    if bars is not None and len(bars) > 5:
        c = bars["close"]
        last = float(c.iloc[-1])
        quote = {"price": price_round(last, last), "change_1d_pct": round(100 * float(c.iloc[-1] / c.iloc[-2] - 1), 2),
                 "change_1w_pct": round(100 * float(c.iloc[-1] / c.iloc[-6] - 1), 2), "volume": float(bars["volume"].iloc[-1]),
                 "high_52w": price_round(float(bars["high"].iloc[-252:].max()), last), "low_52w": price_round(float(bars["low"].iloc[-252:].min()), last),
                 "as_of": str(bars.index[-1].date())}
    fund = fundamental_provider().get_fundamentals(ins.symbol)
    return {**_info(ins), "quote": quote, "fundamentals": fund, "fundamentals_available": fund is not None, "data": data_meta(db, ins)}


INDICATOR_COLS = ["ema20", "ema50", "ema100", "ema200", "sma20", "sma50", "sma200", "vwap20", "bb_upper", "bb_mid", "bb_lower", "rsi",
                  "macd", "macd_signal", "macd_hist", "adx", "atr", "stoch_k", "stoch_d", "mfi", "cci", "obv", "supertrend", "supertrend_dir", "vol_sma20",
                  "geo_upper", "geo_lower", "cup_rim"]  # geometric pattern boundaries (NaN when no pattern is active)


@router.get("/{symbol}/candles", dependencies=[Depends(require("analysis:read"))])
def candles(symbol: str, db: Session = Depends(get_db), interval: str = Query("1d", pattern="^(1d|1w)$"), limit: int = Query(500, ge=50, le=5000)):
    from app.models import DataStatus

    ins = _instrument(db, symbol)
    st = db.get(DataStatus, (ins.id, "1d"))
    # keyed on the last bar and fetch time, so a new or revised bar is never served stale
    key = f"me:candles:{ins.symbol}:{interval}:{limit}:{st.last_bar_ts if st else None}:{st.fetched_at if st else None}"
    cached = get_cache().get_json(key)
    if cached:
        return {**cached, "data": data_meta(db, ins)}
    df = load_bars(db, {ins.id: ins.symbol}).get(ins.symbol)
    if df is None or df.empty:
        raise HTTPException(404, "No data ingested for this symbol")
    if interval == "1w":
        df = df.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    f = build_features(df) if len(df) >= 60 else df
    tail = f.iloc[-limit:]
    out = {"t": [str(i.date()) for i in tail.index]}
    ref = float(tail["close"].iloc[-1])
    price_like = {"open", "high", "low", "close", "ema20", "ema50", "ema100", "ema200", "sma20", "sma50", "sma200", "vwap20",
                  "bb_upper", "bb_mid", "bb_lower", "supertrend", "atr", "macd", "macd_signal", "macd_hist", "geo_upper", "geo_lower", "cup_rim"}
    for c in ["open", "high", "low", "close", "volume"] + [c for c in INDICATOR_COLS if c in tail.columns]:
        # price-scale series keep precision suited to the instrument (5 dp EUR/USD, 10 dp sub-cent tokens)
        dec = price_decimals(ref) + (2 if c in ("atr", "macd", "macd_signal", "macd_hist") else 0) if c in price_like else 4
        out[c] = [None if (v is None or (isinstance(v, float) and not np.isfinite(v))) else round(float(v), dec) for v in tail[c].to_numpy()]
    out["price_decimals"] = price_decimals(ref)
    levels = [lv.__dict__ for lv in sr_levels(f, len(f) - 1)] if "pivot_high" in f.columns else []
    body = {"symbol": ins.symbol, "interval": interval, "bars": out, "levels": levels}
    get_cache().set_json(key, body, ttl=6 * 3600)
    return {**body, "data": data_meta(db, ins)}


@router.get("/{symbol}/analysis", dependencies=[Depends(require("analysis:read"))])
def analysis(symbol: str, db: Session = Depends(get_db), mode: str = Query("hybrid", pattern="^(technical|fundamental|hybrid)$")):
    """Evaluates every strategy on the latest bar using precomputed historical events.
    Cheap: single-symbol features + stored events — the expensive universe replay runs in the scan job."""
    from app.core.markets import market_config
    from engine.strategies import strategy_set

    ins = _instrument(db, symbol)
    market = ins.market or "NSE"
    prof = market_config(market).profile
    run = latest_run(db, market)
    # per scan run and calendar day (freshness flags and days-to-earnings are date-dependent)
    key = f"me:analysis:{ins.symbol}:{mode}:{run.id if run else 0}:{date.today()}"
    cached = get_cache().get_json(key)
    if cached:
        return cached
    df = load_bars(db, {ins.id: ins.symbol}).get(ins.symbol)
    if df is None or len(df) < 60:
        raise HTTPException(404, "Not enough data for analysis")
    cfg = engine_config(db, mode, market)
    reg = db.scalar(select(MarketRegime).where(MarketRegime.market == market).order_by(MarketRegime.as_of.desc()).limit(1))
    ctx = MarketContext(pd.DataFrame(), reg.payload if reg else None, pd.DataFrame(), {})
    f = build_features(df)
    fp = fundamental_provider()
    fund = fp.get_fundamentals(ins.symbol) if market == "NSE" else None
    nd = fp.next_earnings_date(ins.symbol) if market == "NSE" else None
    if prof.asset_class == "EQUITY":
        from datetime import timedelta as _td

        from app.services.events_service import next_earnings_days

        dd = next_earnings_days(db, {ins.id: ins.symbol}, date.today()).get(ins.symbol)
        nd = nd or (date.today() + _td(days=dd) if dd is not None else None)
    meta = data_meta(db, ins, max_lag_days=cfg.validation.max_staleness_days, calendar=prof.calendar)
    events = latest_events(db, market)
    from app.services.strategy_service import scan_strategies

    liq = 1.0
    fx = None
    if market != "NSE":
        from app.services.fx_service import load_fx_book

        fx = load_fx_book(db)
        conv = fx.convert(ins.currency, prof.liquidity_currency)
        liq = conv["rate"] if conv else 1.0
    info_ = {**_info(ins), "volumeless": prof.volumeless}
    specs = scan_strategies(db) if prof.strategy_set == "equity" else list(strategy_set(prof.strategy_set).values())
    evals = Analyzer(cfg).evaluate_symbol(ins.symbol, f, events, ctx, today=date.today(), instrument=info_, fundamentals=fund,
                                          earnings_in_days=(nd - date.today()).days if nd else None, data_meta=meta, market=market,
                                          only_active=False, strategies=specs, calendar=prof.calendar, liquidity_mult=liq,
                                          liquidity_currency=prof.liquidity_currency, short_note=prof.short_note or None)
    active_ = [e for e in evals if e["status"] != "NO_SIGNAL"]
    if active_:
        from app.services.scan_service import enrich_setups

        full_info = {ins.symbol: {**info_, "id": ins.id, "meta": dict(ins.meta or {})}}
        enrich_setups(db, active_, market, full_info, fx, {ins.symbol: df})
        from app.services.ml_service import annotate_setups

        annotate_setups(db, market, active_, {ins.symbol: f}, None, cfg.weights)
    active = [e for e in evals if e["status"] != "NO_SIGNAL"]
    if run and active:  # link to the persisted signal when the scan evaluated the same bar
        ids = {(sg.strategy_key, str(sg.as_of)): sg.id for sg in db.scalars(select(Signal).where(Signal.scan_run_id == run.id, Signal.symbol == ins.symbol))}
        for e in active:
            e["signal_id"] = ids.get((e["strategy"]["id"], e["as_of"]))
    row = f.iloc[-1]
    from engine.features import active_patterns
    from engine.mtf import analyze as mtf_analyze

    past = db.execute(select(Signal, SignalOutcome).outerjoin(SignalOutcome, SignalOutcome.signal_id == Signal.id)
                      .where(Signal.symbol == ins.symbol, Signal.status == "VALID").order_by(Signal.as_of.desc(), Signal.id.desc()).limit(200)).all()
    seen, uniq = set(), []
    for sg, oc in past:  # rescans republish the same session's setup: show each (session, strategy) once
        if (sg.as_of, sg.strategy_key) not in seen:
            seen.add((sg.as_of, sg.strategy_key))
            uniq.append((sg, oc))
    past = uniq[:20]
    out = _json_safe({
        "symbol": ins.symbol, "as_of": str(f.index[-1].date()), "mode": mode, "data": meta,
        "active_setups": sorted(active, key=lambda e: (e["status"] != "VALID", -e["score"])),
        "status": "VALID" if any(e["status"] == "VALID" for e in active) else ("NO_TRADE" if active else "NO_SETUP"),
        "inactive_strategies": [{"id": e["strategy"]["id"], "name": e["strategy"]["name"], "direction": e["direction"]} for e in evals if e["status"] == "NO_SIGNAL"],
        "state": {
            "patterns": active_patterns(row),
            "mtf_long": mtf_analyze(df, "LONG"),
            "structure": {k: (None if pd.isna(row.get(k)) else (bool(row[k]) if isinstance(row[k], (bool, np.bool_)) else round(float(row[k]), 2)))
                          for k in ("swing_high", "swing_low", "higher_high", "higher_low", "lower_high", "lower_low")},
            "indicators": {k: (None if pd.isna(row.get(k)) else round(float(row[k]), 3)) for k in
                           ("rsi", "adx", "macd_hist", "atr", "atr_pct", "vol_ratio20", "vol_ratio50", "ema20", "ema50", "ema200", "mfi", "cci", "stoch_k", "bb_pctb", "obv_slope")},
            "levels": [lv.__dict__ for lv in sr_levels(f, len(f) - 1)],
        },
        "market_regime": ctx.regime_now,
        "signal_history": [{"as_of": str(sg.as_of), "strategy": sg.strategy_key, "direction": sg.direction, "score": sg.score,
                            "outcome": (oc.status if oc else None), "t1_hit": oc.t1_hit if oc else None, "stop_hit": oc.stop_hit if oc else None,
                            "net_return_pct": oc.net_return_pct if oc else None} for sg, oc in past],
        "disclaimer": DISCLAIMER,
    })
    get_cache().set_json(key, out, ttl=3 * 3600)
    return out


@router.get("/{symbol}/events", dependencies=[Depends(require("analysis:read"))])
def stock_events(symbol: str, db: Session = Depends(get_db)):
    """Earnings history (surprises, guidance, historical price reaction), upcoming earnings, relevant
    economic releases and recent news for one instrument."""
    from datetime import datetime, timedelta, timezone

    from app.services.events_service import earnings_for, economic_events, news_for
    from engine.events import earnings_reaction, relevant_events, upcoming_summary

    ins = _instrument(db, symbol)
    today = date.today()
    earn = earnings_for(db, {ins.id: ins.symbol}, today - timedelta(days=800), today + timedelta(days=120))
    past = [e for e in earn if e["event_date"] < str(today)]
    upcoming = [e for e in earn if e["event_date"] >= str(today)]
    bars = load_bars(db, {ins.id: ins.symbol}).get(ins.symbol)
    reaction = earnings_reaction(bars["close"], [e["event_date"] for e in past]) if bars is not None else {"count": 0}
    now = datetime.now(timezone.utc)
    ccys = {ins.meta.get("base"), ins.meta.get("quote")} - {None} if ins.market == "FX" else {ins.currency}
    evs = upcoming_summary(relevant_events(economic_events(db, now - timedelta(hours=1), now + timedelta(days=14)), ins.market, ccys), now, 14, 20)
    nxt = upcoming[0] if upcoming else None
    days = (date.fromisoformat(nxt["event_date"]) - today).days if nxt else None
    return {"symbol": ins.symbol, "market": ins.market,
            "next_earnings": {**nxt, "days_until": days, "warning": f"⚠ Earnings in {days} days — high event risk" if days is not None and days <= 10 else None} if nxt else None,
            "earnings_history": past[::-1][:8], "earnings_reaction": reaction, "economic_events": evs,
            "news": news_for(db, symbols=[ins.symbol], since=now - timedelta(days=30), limit=20),
            "notes": {"news": "Sentiment is context only; it never generates a setup.",
                      "earnings": "Earnings within 3 days block new setups; within 10 days they warn."}}
