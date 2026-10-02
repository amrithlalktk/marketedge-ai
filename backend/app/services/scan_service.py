"""Full-universe scan: features -> regime/breadth -> historical events -> live setups.
Runs in a background worker, never inside a web request. Results are
persisted so the API only reads precomputed data."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Dict, Optional

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.core.cache import get_cache
from app.core.config import get_settings
from app.models import (Backtest, BacktestTrade, DataStatus, MarketRegime, MarketSnapshot, ScanRun, Signal, SignalOutcome)
from app.services.market_data import instrument_maps, load_bars
from app.services.settings_service import engine_config
from engine import ENGINE_VERSION
from engine.analyzer import Analyzer
from engine.backtest import simulate_trade
from engine.config import BacktestConfig
from engine.mtf import trend_state
from engine.strategies import STRATEGIES
from engine.validation import expected_last_session

log = logging.getLogger(__name__)

TRADE_COLUMNS = [c.name for c in BacktestTrade.__table__.columns if c.name not in ("id", "backtest_id")]


def bulk_meta(db: Session, ids: Dict[int, str], today: date, max_lag: int, calendar: str = "weekdays") -> Dict[str, dict]:
    expected = expected_last_session(today, calendar=calendar)
    out = {}
    for st in db.scalars(select(DataStatus).where(DataStatus.interval == "1d", DataStatus.instrument_id.in_(list(ids)))):
        lag = (expected - st.last_bar_ts.date()).days if st.last_bar_ts else None
        out[ids[st.instrument_id]] = {"source": st.source, "is_sample": st.is_sample, "last_bar": str(st.last_bar_ts.date()) if st.last_bar_ts else None,
                                      "fetched_at": st.fetched_at.isoformat() if st.fetched_at else None, "lag_days": lag,
                                      "delayed": lag is None or lag > max_lag, "error": st.error}
    return out


def index_overview(bars: Dict[str, pd.DataFrame], info: Dict[str, dict], meta: Dict[str, dict], symbols=None, periods_per_year: int = 252) -> list:
    """Overview cards: the market's indices by default, or an explicit symbol list (top coins, FX pairs)."""
    rows = []
    week = 7 if periods_per_year >= 365 else 5
    for sym in (symbols if symbols is not None else [k for k in bars if info.get(k, {}).get("is_index")]):
        df = bars.get(sym)
        if df is None or len(df) < 30:
            continue
        c = df["close"]
        rv = float(np.sqrt(periods_per_year) * c.pct_change().iloc[-20:].std() * 100)
        rows.append({
            "symbol": sym, "name": info[sym]["name"], "price": float(f"{float(c.iloc[-1]):.6g}") if c.iloc[-1] < 10 else round(float(c.iloc[-1]), 2),
            "currency": info[sym].get("currency"), "asset_class": info[sym].get("asset_class"),
            "change_1d_pct": round(100 * float(c.iloc[-1] / c.iloc[-2] - 1), 2),
            "change_1w_pct": round(100 * float(c.iloc[-1] / c.iloc[-1 - week] - 1), 2) if len(c) > week + 1 else None,
            "trend": trend_state(df), "realized_vol_20d_pct": round(rv, 1),
            "volume": float(df["volume"].iloc[-1]), "as_of": str(df.index[-1].date()),
            "sparkline": [float(f"{float(x):.6g}") for x in c.iloc[-60:]], "data": meta.get(sym, {}),
        })
    return rows


def _json_safe(obj):
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return str(obj)
    return obj


def _trade_rows(events: pd.DataFrame, backtest_id: int) -> list:
    if events.empty:
        return []
    df = events.rename(columns={"strategy_id": "strategy_key"}).copy()
    for c in ("signal_date", "entry_date", "exit_date", "t1_date"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c]).dt.date
    for c in TRADE_COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[TRADE_COLUMNS].astype(object).where(pd.notna(df[TRADE_COLUMNS]), None)
    rows = df.to_dict("records")
    for r in rows:
        r["backtest_id"] = backtest_id
    return rows


def run_scan(db: Session, market: Optional[str] = None, today: Optional[date] = None) -> ScanRun:
    from app.core.markets import market_config
    from app.services.strategy_service import scan_strategies

    s = get_settings()
    market = market or s.market
    mc = market_config(market)
    prof = mc.profile
    today = today or date.today()
    cfg = engine_config(db, market=market)
    run = ScanRun(market=market, status="running", engine_version=ENGINE_VERSION, config_snapshot={**cfg.to_dict(), "market_profile": prof.id})
    db.add(run)
    db.commit()
    try:
        ids, info = instrument_maps(db, market)
        bars = load_bars(db, ids)
        if mc.benchmark not in bars:
            raise RuntimeError(f"Benchmark {mc.benchmark} for {market} has no data — run ingestion for {market} first")
        meta = bulk_meta(db, ids, today, cfg.validation.max_staleness_days, prof.calendar)
        vix = bars[mc.vix]["close"] if mc.vix and mc.vix in bars else None
        analyzer = Analyzer(cfg, workers=get_settings().scan_workers)
        # in crypto the benchmark (BTC) is itself tradable, so only true indices are excluded
        stocks = {sym: df for sym, df in bars.items() if not info[sym]["is_index"]}
        feats = analyzer.prepare(stocks)
        from app.services.universe_service import membership_for
        from engine.universe import current_members

        membership, delisted, universe = membership_for(db, market, info, bars)
        ctx = analyzer.market_context(bars[mc.benchmark], feats, vix, membership=membership)

        specs = scan_strategies(db)
        events = analyzer.build_events(feats, ctx.regime_df, specs, universe_dates={k: (v["listed_on"], v["delisted_on"]) for k, v in info.items()},
                                       membership=membership, final_symbols=delisted)
        if membership is not None:  # today's setups come only from today's point-in-time members
            live = current_members(membership)
            feats = {k: v for k, v in feats.items() if k in live}

        # no fundamentals / earnings / FX feeds in the lite build: technical setups, liquidity in the market's own currency
        result = analyzer.scan(feats, events, ctx, today=today, instruments=info, meta=meta, market=market,
                               fundamentals={}, earnings={}, strategies=specs, liquidity_mults={},
                               eval_kwargs={"calendar": prof.calendar, "liquidity_currency": prof.liquidity_currency, "short_note": prof.short_note or None})
        enrich_setups(db, result["valid"] + result["no_trade"], market, info, bars)
        allsetups = result["valid"] + result["no_trade"]  # enrichment may add blocking checks (e.g. crypto spread)
        result["valid"] = sorted([x for x in allsetups if x["status"] == "VALID"], key=lambda x: -x["score"])
        result["no_trade"] = [x for x in allsetups if x["status"] != "VALID"]
        if not result["valid"] and not result["market_message"]:
            result["market_message"] = "NO VALID SETUP: no candidate passed every validation check today."

        bt = Backtest(kind="system_events", strategy_key="all", status="done", params={"scan_run_id": run.id, "engine_version": ENGINE_VERSION},
                      result={"trades": int(len(events))}, started_at=run.started_at, finished_at=datetime.now(timezone.utc))
        db.add(bt)
        db.flush()
        rows = _trade_rows(events, bt.id)
        for i in range(0, len(rows), 5000):
            db.execute(insert(BacktestTrade), rows[i : i + 5000])
        run.events_backtest_id = bt.id

        as_of = date.fromisoformat(result["as_of"]) if result["as_of"] else today
        for setup in result["valid"] + result["no_trade"]:
            sig = Signal(scan_run_id=run.id, instrument_id=info[setup["symbol"]]["id"], symbol=setup["symbol"], market=market,
                         strategy_key=setup["strategy"]["id"], direction=setup["direction"], status=setup["status"],
                         as_of=date.fromisoformat(setup["as_of"]), score=setup["score"], rr_t2=setup["rr_t2"],
                         t1_hit_rate=setup["probability"].get("t1_hit_rate"), sample_size=setup["probability"].get("sample_size", 0),
                         is_sample_data=bool(setup["data"].get("is_sample")), payload=_json_safe(setup))
            db.add(sig)
            if setup["status"] == "VALID":
                db.flush()
                # a rescan of the same session republishes the setup; track its outcome only once
                dup = db.scalar(select(SignalOutcome.signal_id).join(Signal, Signal.id == SignalOutcome.signal_id).where(
                    Signal.market == market, Signal.symbol == sig.symbol, Signal.strategy_key == sig.strategy_key,
                    Signal.as_of == sig.as_of, Signal.id != sig.id).limit(1))
                if dup is None:
                    db.add(SignalOutcome(signal_id=sig.id, status="open"))

        if ctx.regime_now:
            db.execute(delete(MarketRegime).where(MarketRegime.market == market, MarketRegime.as_of == date.fromisoformat(ctx.regime_now["as_of"])))
            db.add(MarketRegime(market=market, as_of=date.fromisoformat(ctx.regime_now["as_of"]), regime=ctx.regime_now["regime"],
                                family=ctx.regime_now["family"], volatility=ctx.regime_now["volatility"], payload=_json_safe(ctx.regime_now)))
        sectors = analyzer.sectors(feats, {k: v["sector"] for k, v in info.items() if v["sector"]}, bars[mc.benchmark]["close"])
        perf = [analyzer.strategy_performance(events, spec, market, prof.periods_per_year) for spec in specs]
        any_sample = any(m.get("is_sample") for m in meta.values())
        if market == "CRYPTO":
            top = sorted(feats, key=lambda k: -float(feats[k]["avg_traded_value20"].iloc[-1] or 0))[:12]
            cards = index_overview(bars, info, meta, top, prof.periods_per_year)
        else:
            cards = index_overview(bars, info, meta, None, prof.periods_per_year)
        extra = {}
        if market == "CRYPTO":
            from engine.crypto import btc_dominance

            caps = {k: v["meta"].get("market_cap_usd") for k, v in info.items() if v["meta"].get("market_cap_usd")}
            extra["btc_dominance_pct"] = btc_dominance(caps, mc.benchmark) if caps else None
            extra["btc_dominance_basis"] = ("share of market cap within the tracked universe (provider metadata)" if caps
                                            else "unavailable — provider supplies no market capitalisation")
            extra["stablecoin_flows"] = {"available": False, "note": "No stablecoin-flow feed configured."}
        snaps = {
            "overview": {"indices": cards, "regime": ctx.regime_now, "is_sample": any_sample,
                         "market": market, "profile": prof.name, "benchmark": mc.benchmark, **extra},
            "breadth": {**ctx.breadth_now, "is_sample": any_sample},
            "sectors": {"sectors": sectors, "as_of": result["as_of"], "is_sample": any_sample,
                        "note": "Rankings describe current relative strength and are not forecasts of future performance."},
            "strategy_performance": {"strategies": perf, "is_sample": any_sample},
            "scan_summary": {"market_message": result["market_message"], "valid": len(result["valid"]), "no_trade": len(result["no_trade"]),
                             "candidates_evaluated": result["candidates_evaluated"], "instruments_scanned": result["instruments_scanned"],
                             "as_of": result["as_of"], "is_sample": any_sample,
                             "stale_instruments": sorted(k for k, v in meta.items() if v.get("delayed") and not info[k]["is_index"]
                                                         and not info[k].get("delisted_on"))},  # delisted history is final, not late
        }
        for kind, payload in snaps.items():
            db.add(MarketSnapshot(scan_run_id=run.id, market=market, kind=kind, as_of=as_of, payload=_json_safe(payload)))

        run.as_of = as_of
        run.stats = {k: v for k, v in snaps["scan_summary"].items() if k != "stale_instruments"} | {"events": int(len(events))} \
            | ({"universe": universe} if universe else {})
        run.status, run.finished_at = "done", datetime.now(timezone.utc)
        db.commit()
        resolved = resolve_outcomes(db, feats, cfg.backtest, cfg.levels.max_chase_atr, market)
        try:  # alerts and paper trades react to the freshly scanned bars; never fail the scan for them
            from app.services import alert_service, portfolio_service

            paper = portfolio_service.process(db)
            bar_alerts = alert_service.evaluate_bar_alerts(db, market)
            setup_alerts = alert_service.new_setup_alerts(db, market, [x for x in allsetups if x["status"] == "VALID"])
            run.stats = {**(run.stats or {}), "alerts": {**bar_alerts, "new_setup_fired": setup_alerts}, "paper": paper}
        except Exception:
            log.exception("alert/paper processing after scan failed")
        run.stats = {**run.stats, "outcomes_resolved": resolved}
        db.commit()
        get_cache().invalidate_prefix("me:")
        prewarm(db, market, allsetups)
        return run
    except Exception as exc:
        db.rollback()
        log.exception("scan failed")
        run = db.get(ScanRun, run.id)
        run.status, run.error, run.finished_at = "failed", str(exc)[:2000], datetime.now(timezone.utc)
        db.commit()
        raise


def enrich_setups(db: Session, setups: list, market: str, info: Dict[str, dict], bars: Dict[str, pd.DataFrame]) -> None:
    """Additions after evaluation: per-market strategy switches, crypto derivatives context and spread."""
    deriv = {}
    if market == "CRYPTO":
        from app.services.market_data import load_derivatives
        from engine.crypto import derivatives_context

        deriv = load_derivatives(db, {info[s["symbol"]]["id"]: s["symbol"] for s in setups})
    from app.services.settings_service import disabled_strategies

    off = disabled_strategies(db, market)
    for st in setups:
        sid = st.get("strategy_id") or (st.get("strategy") or {}).get("id")
        if sid in off:  # switched off for this market: still shown, never VALID, with the reason
            st["checks"].append({"name": "Strategy enabled", "passed": False, "severity": "block",
                                 "detail": f"{st.get('strategy_name') or sid} is disabled for {market} live setups: {off[sid].get('reason') or 'admin decision'}"})
            st["explanation"]["risk_factors"].append(st["checks"][-1]["detail"])
        inf = info[st["symbol"]]
        if market == "CRYPTO":
            ctx = derivatives_context(bars[st["symbol"]]["close"], deriv.get(st["symbol"]), st["direction"])
            st["derivatives"] = ctx
            st["checks"].extend(ctx["checks"])
            st["explanation"]["risk_factors"].extend(c["detail"] for c in ctx["checks"])
            st["market_cap_usd"] = inf["meta"].get("market_cap_usd")
            st["spread_bps"] = inf["meta"].get("spread_bps")
            if st["spread_bps"] is not None and st["spread_bps"] > 20:
                st["checks"].append({"name": "Spread", "passed": False, "severity": "block", "detail": f"Bid/ask spread {st['spread_bps']:.1f} bps (max 20)"})
        if st["status"] == "VALID" and any((not c["passed"]) and c["severity"] == "block" for c in st["checks"]):
            st["status"] = "NO_TRADE"


def resolve_outcomes(db: Session, feats: Dict[str, pd.DataFrame], bt: BacktestConfig, max_chase_atr: float, market: str = "NSE") -> int:
    """Forward-track published VALID signals with exactly the backtest rules (live track record)."""
    n = 0
    from app.services.strategy_service import scan_strategies

    custom = {s.id: s for s in scan_strategies(db)}
    pending = db.execute(select(SignalOutcome, Signal).join(Signal, Signal.id == SignalOutcome.signal_id)
                         .where(SignalOutcome.status == "open", Signal.market == market)).all()
    for outcome, sig in pending:
        f = feats.get(sig.symbol)
        if f is None:
            continue
        ts = pd.Timestamp(sig.as_of)
        if ts not in f.index:
            continue
        t = f.index.get_loc(ts)
        spec = STRATEGIES.get(sig.strategy_key) or custom.get(sig.strategy_key)
        hold = spec.max_hold_bars if spec else bt.max_hold_bars
        p = sig.payload
        tr = simulate_trade(f, t, sig.direction, p["stop"], p["targets"][0], p["targets"][1],
                            BacktestConfig(max_hold_bars=hold, partial_at_t1=bt.partial_at_t1, costs=bt.costs), max_chase_atr, sig.symbol, sig.strategy_key)
        if tr is not None:
            outcome.status, outcome.t1_hit, outcome.t2_hit, outcome.stop_hit = "resolved", tr.t1_hit, tr.t2_hit, tr.stop_hit
            outcome.exit_reason, outcome.net_return_pct, outcome.resolved_at = tr.exit_reason, tr.net_return_pct, datetime.now(timezone.utc)
            n += 1
        elif t + 1 < len(f) and t + hold < len(f) - 1:
            outcome.status, outcome.exit_reason, outcome.resolved_at = "skipped", "not_filled", datetime.now(timezone.utc)
            n += 1
    db.commit()
    return n


def provider_is_sample(market: str) -> bool:
    from app.core.markets import market_config

    if market == "NFO":
        return get_settings().options_data_provider == "sample"
    return market_config(market).provider == "sample"


def matches_provider(market: str, is_sample) -> bool:
    """A result is shown only if it came from the market's CURRENT kind of data: after a market switches from
    SAMPLE to a real provider, the old sample scans stay in the database but are never displayed again."""
    return is_sample is None or bool(is_sample) == provider_is_sample(market)


def latest_run(db: Session, market: str) -> Optional[ScanRun]:
    for run in db.scalars(select(ScanRun).where(ScanRun.market == market, ScanRun.status == "done").order_by(ScanRun.id.desc()).limit(25)):
        if matches_provider(market, (run.stats or {}).get("is_sample")):
            return run
    return None


def snapshot_is_sample(db: Session, row) -> Optional[bool]:
    run = db.get(ScanRun, row.scan_run_id) if row.scan_run_id else None
    flag = (run.stats or {}).get("is_sample") if run is not None else None
    if flag is None and isinstance(row.payload, dict):
        data = row.payload.get("data")
        flag = row.payload.get("is_sample", data.get("is_sample") if isinstance(data, dict) else None)
    return flag


def prewarm(db: Session, market: str, setups: list) -> dict:
    """Fill the analysis/candles caches for the symbols users open right after a scan
    (VALID setups first, then the best-scored rest). Never fails the scan."""
    import time

    n = get_settings().prewarm_top_n
    if n <= 0:
        return {}
    ranked = sorted(setups, key=lambda x: (x.get("status") != "VALID", -(x.get("score") or 0)))
    symbols = [x["symbol"] for x in ranked if x.get("symbol")]
    try:  # then the market's most-watched instruments
        from sqlalchemy import func as _f

        from app.models import Instrument, WatchlistItem

        symbols += [sym for (sym,) in db.execute(
            select(Instrument.symbol).join(WatchlistItem, WatchlistItem.instrument_id == Instrument.id)
            .where(Instrument.market == market).group_by(Instrument.symbol).order_by(_f.count().desc()).limit(n))]
    except Exception:
        log.debug("prewarm watchlist lookup failed", exc_info=True)
    symbols = list(dict.fromkeys(symbols))[:n]
    from app.api.routes.stocks import analysis, candles

    t0, done = time.perf_counter(), 0
    for sym in symbols:
        try:
            analysis(sym, db=db, mode="hybrid")
            candles(sym, db=db, interval="1d", limit=500)
            done += 1
        except Exception:
            log.debug("prewarm %s failed", sym, exc_info=True)
    log.info("prewarmed %d/%d %s symbols in %.1fs", done, len(symbols), market, time.perf_counter() - t0)
    return {"prewarmed": done}


_EVENTS_MEMO: "dict[int, pd.DataFrame]" = {}


def latest_events(db: Session, market: str) -> pd.DataFrame:
    run = latest_run(db, market)
    if run is None or run.events_backtest_id is None:
        return pd.DataFrame()
    # a stored events backtest never changes, so a per-process memo is safe (skips the JSON decode)
    memo = _EVENTS_MEMO.get(run.events_backtest_id)
    if memo is not None:
        return memo.copy()  # callers may add columns
    key = f"me:events:{run.events_backtest_id}"
    cached = get_cache().get_frame(key)
    if cached is not None:
        _remember_events(run.events_backtest_id, cached)
        return cached
    q = select(*[getattr(BacktestTrade, c) for c in TRADE_COLUMNS]).where(BacktestTrade.backtest_id == run.events_backtest_id)
    df = pd.DataFrame(db.execute(q).all(), columns=TRADE_COLUMNS).rename(columns={"strategy_key": "strategy_id"})
    for c in ("signal_date", "entry_date", "exit_date"):
        df[c] = df[c].astype(str)
    df["t1_date"] = df["t1_date"].map(lambda d: str(d) if d is not None and d == d else None)
    get_cache().set_frame(key, df, ttl=6 * 3600)
    _remember_events(run.events_backtest_id, df)
    return df


def _remember_events(backtest_id: int, df: pd.DataFrame) -> None:
    if len(_EVENTS_MEMO) >= 8:  # one per market plus headroom
        _EVENTS_MEMO.pop(next(iter(_EVENTS_MEMO)))
    _EVENTS_MEMO[backtest_id] = df
