"""Ingestion (provider -> PostgreSQL) and loading (PostgreSQL -> DataFrames).
The engine always reads from the database, never straight from a provider,
so every analysis is reproducible from stored data."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DataStatus, Instrument, MarketBar, Sector
from app.providers.base import MarketDataProvider
from engine.validation import expected_last_session

log = logging.getLogger(__name__)


def _upsert_stmt(db: Session):
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(MarketBar)
    return stmt.on_conflict_do_update(
        index_elements=["instrument_id", "interval", "ts"],
        set_={k: getattr(stmt.excluded, k) for k in ("open", "high", "low", "close", "volume", "source")},
    )


def sync_instruments(db: Session, provider: MarketDataProvider, market: str) -> Dict[str, Instrument]:
    sectors = {s.name: s for s in db.scalars(select(Sector))}
    existing = {i.symbol: i for i in db.scalars(select(Instrument))}
    for ins in provider.list_instruments(market):
        if ins.sector and ins.sector not in sectors:
            sectors[ins.sector] = Sector(name=ins.sector)
            db.add(sectors[ins.sector])
            db.flush()
        row = existing.get(ins.symbol) or Instrument(symbol=ins.symbol)
        row.name, row.asset_class, row.exchange, row.currency = ins.name, ins.asset_class, ins.exchange, ins.currency
        row.market = market
        row.meta = {**(row.meta or {}), **(ins.extra or {})}
        row.sector_id = sectors[ins.sector].id if ins.sector else None
        row.lot_size, row.is_index, row.is_sample = ins.lot_size, ins.is_index, ins.is_sample or provider.is_sample
        row.listed_on = date.fromisoformat(ins.listed_on) if ins.listed_on else None
        row.delisted_on = date.fromisoformat(ins.delisted_on) if ins.delisted_on else None
        row.is_active = True
        if row.id is None:
            db.add(row)
        existing[ins.symbol] = row
    # Never mix SAMPLE and real data in one market: when a market switches provider kind
    # (sample ↔ live), instruments of the other kind are deactivated (kept, not deleted — switching
    # back reactivates them via the loop above).
    rejected = getattr(provider, "rejected", set())  # e.g. stablecoins the provider filters out
    for row in existing.values():
        if row.market == market and row.is_active and (bool(row.is_sample) != bool(provider.is_sample) or row.symbol in rejected):
            row.is_active = False
    db.commit()
    return existing


def ingest(db: Session, provider: MarketDataProvider, market: str, symbols: Optional[Iterable[str]] = None,
           interval: str = "1d", full: bool = False) -> dict:
    if hasattr(provider, "preflight"):
        provider.preflight()  # e.g. Upstox not connected today: fail ONCE with a clear message, not once per symbol
    instruments = sync_instruments(db, provider, market)
    in_market = {k: v for k, v in instruments.items() if v.market == market and v.is_active}
    targets = [in_market[s] for s in (symbols or in_market.keys()) if s in in_market]
    stats = {"instruments": len(targets), "bars": 0, "errors": {}}
    if hasattr(provider, "prefetch") and interval == "1d":
        try:  # e.g. Upstox: today's session bar for all symbols in a few batch requests
            stats["today_bars"] = provider.prefetch([t.symbol for t in targets])
        except Exception as exc:
            log.warning("prefetch of today's bars failed: %s", exc)
    for ins in targets:
        status = db.get(DataStatus, (ins.id, interval))
        start = None
        if status and status.last_bar_ts and not full:
            start = (status.last_bar_ts - timedelta(days=7)).date()  # small overlap re-writes revised bars
        try:
            df, meta = provider.get_ohlcv(ins.symbol, interval, start=start)
        except Exception as exc:
            log.exception("ingest failed for %s", ins.symbol)
            stats["errors"][ins.symbol] = str(exc)[:300]
            status = status or DataStatus(instrument_id=ins.id, interval=interval, source=provider.name)
            status.error, status.fetched_at = str(exc)[:1000], datetime.now(timezone.utc)
            db.merge(status)
            db.commit()
            continue
        src = meta.source[:32]
        rows = [{"instrument_id": ins.id, "interval": interval, "ts": ts.to_pydatetime().replace(tzinfo=None),
                 "open": float(o), "high": float(h), "low": float(lo), "close": float(c), "volume": float(v), "source": src}
                for ts, o, h, lo, c, v in zip(df.index, df["open"], df["high"], df["low"], df["close"], df["volume"])]
        if rows:
            db.execute(_upsert_stmt(db), rows)  # executemany -> batched "insertmanyvalues"
        stats["bars"] += len(rows)
        last = df.index.max().to_pydatetime().replace(tzinfo=None) if len(df) else (status.last_bar_ts if status else None)
        db.merge(DataStatus(instrument_id=ins.id, interval=interval, source=meta.source[:32], is_sample=meta.is_sample,
                            last_bar_ts=last, fetched_at=datetime.now(timezone.utc), error=None))
        db.commit()
        if interval == "1d" and hasattr(provider, "get_derivatives") and not ins.is_index:
            try:
                stats["derivatives"] = stats.get("derivatives", 0) + _store_derivatives(db, ins, provider.get_derivatives(ins.symbol), provider.name)
            except Exception as exc:  # context data only; never fail ingestion for it
                log.warning("derivatives for %s failed: %s", ins.symbol, exc)
    return stats


def _store_derivatives(db: Session, ins: Instrument, df, source: str) -> int:
    from app.models import CryptoDerivative

    if df is None or len(df) == 0:
        return 0
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    clean = lambda v: None if v is None or v != v else float(v)
    rows = [{"instrument_id": ins.id, "as_of": ts.date(), "funding_rate": clean(r.get("funding_rate")), "open_interest": clean(r.get("open_interest")),
             "long_short_ratio": clean(r.get("long_short_ratio")), "source": source[:32]} for ts, r in df.iterrows()]
    stmt = insert(CryptoDerivative)
    db.execute(stmt.on_conflict_do_update(index_elements=["instrument_id", "as_of"],
                                          set_={k: getattr(stmt.excluded, k) for k in ("funding_rate", "open_interest", "long_short_ratio", "source")}), rows)
    db.commit()
    return len(rows)


def load_derivatives(db: Session, instrument_ids: Dict[int, str]) -> Dict[str, pd.DataFrame]:
    from app.models import CryptoDerivative

    if not instrument_ids:
        return {}
    rows = db.execute(select(CryptoDerivative).where(CryptoDerivative.instrument_id.in_(list(instrument_ids))).order_by(CryptoDerivative.as_of)).scalars().all()
    out: Dict[str, list] = {}
    for r in rows:
        out.setdefault(instrument_ids[r.instrument_id], []).append((pd.Timestamp(r.as_of), r.funding_rate, r.open_interest, r.long_short_ratio))
    return {k: pd.DataFrame(v, columns=["ts", "funding_rate", "open_interest", "long_short_ratio"]).set_index("ts") for k, v in out.items()}


def load_bars(db: Session, instrument_ids: Dict[int, str], interval: str = "1d", since: Optional[date] = None) -> Dict[str, pd.DataFrame]:
    if not instrument_ids:
        return {}
    q = select(MarketBar.instrument_id, MarketBar.ts, MarketBar.open, MarketBar.high, MarketBar.low, MarketBar.close, MarketBar.volume).where(
        MarketBar.interval == interval, MarketBar.instrument_id.in_(list(instrument_ids)))
    if since:
        q = q.where(MarketBar.ts >= datetime.combine(since, datetime.min.time()))
    df = pd.DataFrame(db.execute(q.order_by(MarketBar.instrument_id, MarketBar.ts)).all(),
                      columns=["iid", "ts", "open", "high", "low", "close", "volume"])
    out = {}
    for iid, g in df.groupby("iid"):
        out[instrument_ids[iid]] = g.drop(columns="iid").set_index(pd.DatetimeIndex(g["ts"])).drop(columns="ts")
    return out


def data_meta(db: Session, instrument: Instrument, interval: str = "1d", today: Optional[date] = None, max_lag_days: int = 4,
              calendar: str = "weekdays") -> dict:
    st = db.get(DataStatus, (instrument.id, interval))
    if st is None:
        return {"source": None, "is_sample": instrument.is_sample, "last_bar": None, "fetched_at": None, "delayed": True, "error": "No data ingested"}
    expected = expected_last_session(today or date.today(), calendar=calendar)
    lag = (expected - st.last_bar_ts.date()).days if st.last_bar_ts else None
    return {
        "source": st.source, "is_sample": st.is_sample, "last_bar": str(st.last_bar_ts.date()) if st.last_bar_ts else None,
        "fetched_at": st.fetched_at.isoformat() if st.fetched_at else None, "lag_days": lag,
        "delayed": lag is None or lag > max_lag_days, "error": st.error,
    }


def provider_status(db: Session) -> List[dict]:
    rows = db.execute(select(DataStatus.source, DataStatus.is_sample, func.count(), func.max(DataStatus.last_bar_ts), func.max(DataStatus.fetched_at),
                             func.count(DataStatus.error)).group_by(DataStatus.source, DataStatus.is_sample)).all()
    return [{"source": r[0], "is_sample": r[1], "instruments": r[2], "last_bar": str(r[3]) if r[3] else None,
             "last_fetch": r[4].isoformat() if r[4] else None, "errors": int(r[5] or 0)} for r in rows]


def instrument_maps(db: Session, market: Optional[str] = None) -> Tuple[Dict[int, str], Dict[str, dict]]:
    q = select(Instrument).where(Instrument.is_active.is_(True))
    if market:
        q = q.where(Instrument.market == market)
    ids, info = {}, {}
    for i in db.scalars(q):
        ids[i.id] = i.symbol
        info[i.symbol] = {"id": i.id, "symbol": i.symbol, "name": i.name, "exchange": i.exchange, "currency": i.currency,
                          "sector": i.sector.name if i.sector else None, "is_index": i.is_index, "is_sample": i.is_sample,
                          "asset_class": i.asset_class, "lot_size": i.lot_size,
                          "listed_on": str(i.listed_on) if i.listed_on else None, "delisted_on": str(i.delisted_on) if i.delisted_on else None,
                          "market": i.market, "meta": dict(i.meta or {}), "volumeless": i.asset_class == "FOREX"}
    return ids, info
