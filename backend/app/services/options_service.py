"""NIFTY options: chain ingestion (provider → DB) and analysis (DB → engine → snapshots/signals).
Runs in the daily job; API endpoints read the persisted results."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.cache import get_cache
from app.core.config import get_settings
from app.models import (Instrument, MarketSnapshot, OptionChainRow, OptionContract, OptionIVHistory, ScanRun, Signal)
from app.providers.registry import market_provider, options_provider
from app.services.market_data import ingest, instrument_maps, load_bars
from app.services.scan_service import _json_safe
from app.services.settings_service import engine_config
from engine import ENGINE_VERSION
from engine.analyzer import Analyzer
from engine.features import build_features
from engine.options import chain as ch
from engine.options.analyzer import analyze_options, index_events
from engine.options.strategies import Leg, analyze_structure, capital_and_reward, leg_from_row

log = logging.getLogger(__name__)
MARKET = "NFO"


def _dialect_insert(db: Session, model):
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(model)


def _underlying(db: Session) -> Instrument:
    sym = get_settings().options_underlying
    ins = db.scalar(select(Instrument).where(Instrument.symbol == sym))
    if ins is None:
        raise RuntimeError(f"Underlying {sym} not found — run equity/index ingestion first")
    return ins


def ingest_chain(db: Session) -> dict:
    s = get_settings()
    und = _underlying(db)
    prov = options_provider()
    snap = prov.get_option_chain(und.symbol)
    c = snap.chain
    existing = {(k.expiry, k.strike, k.option_type): k.id for k in db.scalars(select(OptionContract).where(OptionContract.underlying_id == und.id))}
    new = [{"underlying_id": und.id, "expiry": e, "strike": float(k), "option_type": t, "lot_size": snap.lot_size}
           for e, k, t in zip(c["expiry"], c["strike"], c["option_type"]) if (e, float(k), t) not in existing]
    if new:
        db.execute(_dialect_insert(db, OptionContract).on_conflict_do_nothing(), new)
        db.flush()
        existing = {(k.expiry, k.strike, k.option_type): k.id for k in db.scalars(select(OptionContract).where(OptionContract.underlying_id == und.id))}
    ts = snap.as_of.astimezone(timezone.utc)
    if "oi_change" not in c.columns or c["oi_change"].isna().all():
        # provider gives OI only (e.g. Angel One): change = OI now − OI in the previous stored snapshot
        prev_ts = db.scalar(select(func.max(OptionChainRow.snapshot_ts)).join(OptionContract, OptionContract.id == OptionChainRow.contract_id)
                            .where(OptionContract.underlying_id == und.id, OptionChainRow.snapshot_ts < ts))
        prev = {cid: oi for cid, oi in db.execute(select(OptionChainRow.contract_id, OptionChainRow.oi).where(OptionChainRow.snapshot_ts == prev_ts))} if prev_ts else {}
        c = c.copy()
        c["oi_change"] = [float(oi) - prev.get(existing[(e, float(k), t)], float(oi)) for e, k, t, oi in zip(c["expiry"], c["strike"], c["option_type"], c["oi"])]
    rows = [{"contract_id": existing[(r.expiry, float(r.strike), r.option_type)], "snapshot_ts": ts, "underlying_price": snap.spot,
             "bid": float(r.bid), "ask": float(r.ask), "ltp": float(r.ltp), "volume": float(r.volume), "oi": float(r.oi),
             "oi_change": float(r.oi_change), "iv": (float(r.iv) if "iv" in c.columns and pd.notna(r.iv) else None), "source": snap.meta.source[:32]}
            for r in c.itertuples()]
    stmt = _dialect_insert(db, OptionChainRow)
    stmt = stmt.on_conflict_do_update(index_elements=["contract_id", "snapshot_ts"],
                                      set_={k: getattr(stmt.excluded, k) for k in ("underlying_price", "bid", "ask", "ltp", "volume", "oi", "oi_change", "iv", "source")})
    db.execute(stmt, rows)
    seeded = 0
    hist = prov.get_iv_history(und.symbol)
    if hist is not None:
        have = {d for (d,) in db.execute(select(OptionIVHistory.as_of).where(OptionIVHistory.underlying_id == und.id))}
        seed = [{"underlying_id": und.id, "as_of": d.date(), "atm_iv": float(v), "source": f"{prov.name}-history"[:32]}
                for d, v in hist.dropna().items() if d.date() not in have and d.date() < snap.as_of.date()]
        if seed:
            db.execute(_dialect_insert(db, OptionIVHistory).on_conflict_do_nothing(), seed)
            seeded = len(seed)
    db.commit()
    intraday = {}
    try:  # intraday bars for VWAP / intraday timeframes, when the market provider supports them
        intraday = ingest(db, market_provider(), s.market, symbols=[und.symbol], interval="5m", full=True)
    except NotImplementedError:
        intraday = {"note": "provider has no intraday bars"}
    except Exception as exc:  # pragma: no cover - provider specific
        log.warning("intraday ingest failed: %s", exc)
        intraday = {"error": str(exc)[:200]}
    return {"contracts": len(rows), "snapshot": snap.as_of.isoformat(), "new_contracts": len(new), "iv_history_seeded": seeded,
            "intraday": {k: v for k, v in intraday.items() if k != "errors"}, "is_sample": snap.meta.is_sample}


def _load_snapshot(db: Session, und: Instrument, which: int = 0):
    """which=0 latest snapshot, 1 = previous one."""
    stamps = [t for (t,) in db.execute(select(OptionChainRow.snapshot_ts).join(OptionContract, OptionContract.id == OptionChainRow.contract_id)
                                       .where(OptionContract.underlying_id == und.id).distinct().order_by(OptionChainRow.snapshot_ts.desc()).limit(which + 1))]
    if len(stamps) <= which:
        return None, None, None, None
    ts = stamps[which]
    q = (select(OptionContract.expiry, OptionContract.strike, OptionContract.option_type, OptionContract.lot_size, OptionChainRow.bid, OptionChainRow.ask,
                OptionChainRow.ltp, OptionChainRow.volume, OptionChainRow.oi, OptionChainRow.oi_change, OptionChainRow.iv,
                OptionChainRow.underlying_price, OptionChainRow.source)
         .join(OptionChainRow, OptionChainRow.contract_id == OptionContract.id)
         .where(OptionContract.underlying_id == und.id, OptionChainRow.snapshot_ts == ts))
    df = pd.DataFrame(db.execute(q).all(), columns=["expiry", "strike", "option_type", "lot_size", "bid", "ask", "ltp", "volume", "oi", "oi_change", "iv", "spot", "source"])
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return df, ts, float(df["spot"].iloc[0]), int(df["lot_size"].iloc[0])


def _chain_meta(df: pd.DataFrame, ts: datetime, is_sample: bool, today: date, max_lag: int) -> dict:
    from engine.validation import expected_last_session

    lag = (expected_last_session(today) - ts.astimezone(ch_ist()).date()).days
    return {"source": str(df["source"].iloc[0]), "is_sample": is_sample, "last_bar": ts.isoformat(), "snapshot_ts": ts.isoformat(),
            "lag_days": lag, "delayed": lag > max_lag, "error": None}


def ch_ist():
    from engine.options.pricing import IST
    return IST


def run_options(db: Session, today: Optional[date] = None) -> ScanRun:
    s = get_settings()
    today = today or date.today()
    cfg = engine_config(db)
    run = ScanRun(market=MARKET, status="running", engine_version=ENGINE_VERSION, config_snapshot=cfg.to_dict())
    db.add(run)
    db.commit()
    try:
        und = _underlying(db)
        chain_df, ts, spot, lot = _load_snapshot(db, und)
        if chain_df is None:
            raise RuntimeError("No option chain snapshot stored — run options ingestion first")
        prev_df, _, _, _ = _load_snapshot(db, und, 1)
        prev_ltp = prev_df.assign(strike=prev_df["strike"].astype(float)).set_index(["expiry", "strike", "option_type"])["ltp"] if prev_df is not None else None

        ids, info = instrument_maps(db, s.market)
        bars = load_bars(db, ids)
        analyzer = Analyzer(cfg, workers=get_settings().scan_workers)
        stocks = {k: v for k, v in bars.items() if not info[k]["is_index"]}
        feats = analyzer.prepare(stocks)
        vix = bars[s.vix_symbol]["close"] if s.vix_symbol and s.vix_symbol in bars else None
        ctx = analyzer.market_context(bars[und.symbol], feats, vix)  # same regime definition as the equity scan
        fi = build_features(bars[und.symbol])
        events = index_events(analyzer, fi, und.symbol, ctx.regime_df)
        m5 = load_bars(db, {und.id: und.symbol}, interval="5m").get(und.symbol)
        ivh = pd.Series({pd.Timestamp(d): v for d, v in db.execute(select(OptionIVHistory.as_of, OptionIVHistory.atm_iv)
                                                                    .where(OptionIVHistory.underlying_id == und.id, OptionIVHistory.as_of < ts.date())
                                                                    .order_by(OptionIVHistory.as_of))}, dtype=float)
        meta = _chain_meta(chain_df, ts, und.is_sample, today, cfg.validation.max_staleness_days)
        chain_cfg = ch.ChainConfig(r=s.risk_free_rate, q=s.dividend_yield)
        evs = []  # no economic calendar in the lite build
        out = analyze_options(analyzer=analyzer, symbol=und.symbol, display_name=s.options_display_name, index_features=fi, events=events,
                              ctx=ctx, chain_raw=chain_df.drop(columns=["spot", "source", "lot_size"]), spot=spot, as_of=ts.astimezone(ch_ist()),
                              lot_size=lot, iv_history=ivh if len(ivh) else None, intraday_5m=m5, today=today, chain_meta=meta, prev_ltp=prev_ltp,
                              chain_cfg=chain_cfg, macro_events=evs)
        if meta["delayed"]:
            out["market_message"] = "⚠ DATA DELAYED — option chain snapshot is stale; no setups are published."
            for st in out["option_setups"]:
                st["status"] = "NO_TRADE"
                st.setdefault("checks", []).append({"name": "Chain freshness", "passed": False, "severity": "block", "detail": f"Snapshot lag {meta['lag_days']} days"})
        atm = out["iv"]["atm_iv_near_pct"]
        if atm is not None:
            stmt = _dialect_insert(db, OptionIVHistory)
            db.execute(stmt.on_conflict_do_update(index_elements=["underlying_id", "as_of"], set_={"atm_iv": stmt.excluded.atm_iv, "source": stmt.excluded.source}),
                       [{"underlying_id": und.id, "as_of": ts.astimezone(ch_ist()).date(), "atm_iv": atm / 100, "source": meta["source"][:32]}])
        for st in out["option_setups"]:
            if "contract" not in st:
                continue
            p = st["probability"]
            db.add(Signal(scan_run_id=run.id, instrument_id=und.id, symbol=st["contract"]["label"][:64], market=MARKET,
                          strategy_key=f"opt:{st['underlying']['strategy']['id']}", direction="LONG" if st["direction"] == "BULLISH" else "SHORT",
                          status=st["status"], as_of=date.fromisoformat(st["as_of"]), score=st["score"], rr_t2=st["rr"],
                          t1_hit_rate=p.get("t1_hit_rate"), sample_size=p.get("sample_size") or 0, is_sample_data=bool(meta["is_sample"]),
                          payload=_json_safe(st)))
        db.add(MarketSnapshot(scan_run_id=run.id, market=MARKET, kind="options", as_of=ts.astimezone(ch_ist()).date(), payload=_json_safe(out)))
        run.as_of = ts.astimezone(ch_ist()).date()
        run.stats = {"is_sample": bool(meta["is_sample"]), "option_setups": len(out["option_setups"]), "valid": sum(x["status"] == "VALID" for x in out["option_setups"]),
                     "strategies_proposed": len(out["strategies"]["proposed"]), "index_events": int(len(events)), "status": out["status"],
                     "market_message": out["market_message"]}
        run.status, run.finished_at = "done", datetime.now(timezone.utc)
        db.commit()
        get_cache().invalidate_prefix("me:opt:")
        try:
            from app.services.alert_service import options_alerts

            run.stats = {**(run.stats or {}), "unusual_options_alerts": options_alerts(db, out["chain"].get(out["oi"]["expiry"], []))}
            db.commit()
        except Exception:
            log.exception("options alerts failed")
        return run
    except Exception as exc:
        db.rollback()
        log.exception("options analysis failed")
        run = db.get(ScanRun, run.id)
        run.status, run.error, run.finished_at = "failed", str(exc)[:2000], datetime.now(timezone.utc)
        db.commit()
        raise


def enriched_latest_chain(db: Session):
    s = get_settings()
    und = _underlying(db)
    df, ts, spot, lot = _load_snapshot(db, und)
    if df is None:
        return None
    c = ch.enrich(df.drop(columns=["spot", "source", "lot_size"]), spot, ts.astimezone(ch_ist()), ch.ChainConfig(r=s.risk_free_rate, q=s.dividend_yield), lot)
    return {"chain": c, "spot": spot, "lot_size": lot, "as_of": ts, "underlying": und, "source": str(df["source"].iloc[0])}


def evaluate_legs(db: Session, legs: List[dict]) -> dict:
    """Custom payoff builder over the latest stored chain."""
    snap = enriched_latest_chain(db)
    if snap is None:
        raise LookupError("No option chain stored")
    c = snap["chain"]
    built: List[Leg] = []
    expiries = set()
    for spec in legs:
        e = date.fromisoformat(str(spec["expiry"]))
        row = c[(c["expiry"] == e) & (c["strike"] == float(spec["strike"])) & (c["option_type"] == spec["option_type"])]
        if row.empty:
            raise ValueError(f"Contract not found: {spec['strike']} {spec['option_type']} {spec['expiry']}")
        built.append(leg_from_row(row.iloc[0], 1 if spec["side"] == "BUY" else -1, int(spec.get("lots", 1))))
        expiries.add(e)
    if len(expiries) != 1:
        raise ValueError("All legs must share one expiry (calendar spreads are not modelled at expiry)")
    e = expiries.pop()
    sub = c[c["expiry"] == e]
    atm = ch.atm_iv(c, e)
    bars = load_bars(db, {snap["underlying"].id: snap["underlying"].symbol}).get(snap["underlying"].symbol)
    sessions = max(len(pd.bdate_range(snap["as_of"].date(), e)) - 1, 1)
    a = analyze_structure(built, snap["spot"], float(sub["forward"].iloc[0]), float(sub["T"].iloc[0]), atm or float(np.nanmean(sub["iv"])),
                          snap["lot_size"], bars["close"] if bars is not None else None, sessions)
    return {"legs": [lg.to_dict() for lg in built], "expiry": str(e), "spot": snap["spot"], "lot_size": snap["lot_size"],
            "as_of": snap["as_of"].astimezone(ch_ist()).isoformat(), **a,
            **capital_and_reward(a, built, float(sub["forward"].iloc[0]), snap["lot_size"])}
