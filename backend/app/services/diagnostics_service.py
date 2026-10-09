"""Losing-trade diagnostics on the stored price history (read-only: nothing is written).

`build_history` replays every strategy over the stored bars exactly like the daily scan does
(same features, point-in-time universe, regimes and level rules). `report` then measures:
data problems, how all historical trades and the would-have-been-published ones ended, how the
stopped trades failed, alternative publish gates (design period vs a later out-of-sample period)
and the live track record.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from engine import diagnostics as dg

log = logging.getLogger(__name__)


def build_history(db: Session, market: str = "NSE", candidates: bool = True) -> Tuple[pd.DataFrame, Dict[str, dict], Dict[str, pd.DataFrame]]:
    from app.core.markets import market_config
    from app.services.market_data import instrument_maps, load_bars
    from app.services.settings_service import engine_config
    from app.services.strategy_service import scan_strategies
    from app.services.universe_service import membership_for
    from engine.analyzer import Analyzer

    mc = market_config(market)
    ids, info = instrument_maps(db, market)
    bars = load_bars(db, ids)
    analyzer = Analyzer(engine_config(db, market=market), workers=get_settings().scan_workers)
    feats = analyzer.prepare({k: v for k, v in bars.items() if not info[k]["is_index"]})
    membership, delisted, _ = membership_for(db, market, info, bars)
    vix = bars[mc.vix]["close"] if mc.vix and mc.vix in bars else None
    ctx = analyzer.market_context(bars[mc.benchmark], feats, vix, membership=membership)
    strategies = scan_strategies(db)
    if candidates:  # version-2 rules under test; relative strength needs the benchmark
        from engine.candidates import CANDIDATES, add_relative_strength

        feats = {k: add_relative_strength(v, bars[mc.benchmark]["close"]) for k, v in feats.items()}
        strategies = strategies + list(CANDIDATES.values())
    events = analyzer.build_events(feats, ctx.regime_df, strategies,
                                   universe_dates={k: (v["listed_on"], v["delisted_on"]) for k, v in info.items()},
                                   membership=membership, final_symbols=delisted)
    return events, info, bars


def policies(cfg) -> list:
    v = cfg.validation
    base = dict(min_score=v.min_score, min_rr=v.min_rr_t2, max_stop_pct=v.max_stop_pct)
    return [
        dg.GatePolicy("current (n≥30, mean R > 0, finest slice)", min_sample=v.min_sample_size, **base),
        dg.GatePolicy("no score slicing (n≥30, mean R > 0)", min_sample=v.min_sample_size, levels=(1, 2), **base),
        dg.GatePolicy("n≥100, mean R > 0", min_sample=100, levels=(1, 2), **base),
        dg.GatePolicy("n≥100, mean R − 1 SE > 0", min_sample=100, lcb_z=1.0, levels=(1, 2), **base),
        dg.GatePolicy("n≥100, mean R − 1.64 SE > 0", min_sample=100, lcb_z=1.645, levels=(1, 2), **base),
        # predeclared acceptance candidates: regime cells only (no fallback to all regimes), plus a last-12-months check
        dg.GatePolicy("A: regime cell n≥100, mean R − 1 SE > 0", min_sample=100, lcb_z=1.0, levels=(1,), **base),
        dg.GatePolicy("B: A + last 12 months n≥30, mean R > 0", min_sample=100, lcb_z=1.0, levels=(1,), recent_min_n=30, **base),
        dg.GatePolicy("C: B without the setup-score rule", min_sample=100, lcb_z=1.0, levels=(1,), recent_min_n=30, use_score=False, **base),
    ]


def strategy_table(events: pd.DataFrame, split: str) -> list:
    """Every strategy's raw results (no gate) before and after the split: is there an edge, and does it last?"""
    e = events.assign(_ts=pd.to_datetime(events["signal_date"]))
    rows = []
    for sid, g in e.groupby("strategy_id"):
        rows.append({"strategy": sid, "design": dg.outcome_stats(g[g["_ts"] < split]), "out_of_sample": dg.outcome_stats(g[g["_ts"] >= split]),
                     "out_of_sample_by_regime": dg.by_group(g[g["_ts"] >= split], "regime_family", min_trades=20)})
    return sorted(rows, key=lambda x: -(x["out_of_sample"].get("expectancy_r") or -9))


def track_record(db: Session, market: str, events: Optional[pd.DataFrame] = None) -> Dict:
    """Live forward-tracked results, and whether each resolved idea matches the replay of the same signal."""
    from app.models import Signal, SignalOutcome

    rows = db.execute(select(Signal, SignalOutcome).join(SignalOutcome, SignalOutcome.signal_id == Signal.id).where(Signal.market == market)).all()
    out = {"published": len(rows), "open": sum(o.status == "open" for _, o in rows), "not_filled": sum(o.status == "skipped" for _, o in rows)}
    done = [(s, o) for s, o in rows if o.status == "resolved"]
    out.update(resolved=len(done), t1_hit=sum(bool(o.t1_hit) for _, o in done), stop_hit=sum(bool(o.stop_hit) for _, o in done),
               ideas=[{"symbol": s.symbol, "strategy": s.strategy_key, "as_of": str(s.as_of), "status": o.status, "t1_hit": o.t1_hit,
                       "stop_hit": o.stop_hit, "exit_reason": o.exit_reason, "net_return_pct": o.net_return_pct} for s, o in rows])
    if events is not None and not events.empty and done:
        ev = events.assign(signal_date=pd.to_datetime(events["signal_date"]).dt.date.astype(str)).set_index(["symbol", "strategy_id", "signal_date"])
        same = diff = 0
        for s, o in done:
            key = (s.symbol, s.strategy_key, str(s.as_of))
            if key in ev.index:
                r = ev.loc[key]
                r = r.iloc[0] if isinstance(r, pd.DataFrame) else r
                if (bool(r["t1_hit"]), bool(r["stop_hit"])) == (bool(o.t1_hit), bool(o.stop_hit)):
                    same += 1
                else:
                    diff += 1
        out["replay_agreement"] = {"same": same, "different": diff}
    return out


def report(db: Session, market: str = "NSE", events: Optional[pd.DataFrame] = None, info: Optional[dict] = None,
           bars: Optional[dict] = None) -> Dict:
    from app.services.settings_service import engine_config

    if events is None:
        events, info, bars = build_history(db, market)
    if events.empty:
        return {"market": market, "events": 0, "note": "no historical trades"}
    info = info or {}
    cfg = engine_config(db, market=market)
    events = events.copy()
    events["sector"] = events["symbol"].map({k: v.get("sector") for k, v in info.items()}).fillna("Unknown")
    events["year"] = pd.to_datetime(events["signal_date"]).dt.year.astype(str)

    # --- data audit
    stock_bars = {k: v for k, v in (bars or {}).items() if not info.get(k, {}).get("is_index")}
    gaps = dg.corporate_action_gaps(stock_bars)
    events["near_corporate_action"] = dg.trades_spanning(events, gaps)
    data = {"instruments": len(stock_bars), "bars": int(sum(len(v) for v in stock_bars.values())),
            "suspected_unadjusted_corporate_actions": int(len(gaps)), "symbols_affected": int(gaps["symbol"].nunique()) if len(gaps) else 0,
            "examples": gaps.head(15).to_dict("records"),
            "trades_near_suspected_action_pct": round(100 * float(events["near_corporate_action"].mean()), 1)}

    # --- every historical trade, and what the live gate would have published
    current = policies(cfg)[0]
    g = dg.gate_replay(events, current)
    pub = g[g["published"]]
    by = lambda df, col: dg.by_group(df, col, min_trades=20)  # noqa: E731
    return {
        "market": market,
        "period": [str(pd.to_datetime(events["signal_date"]).min().date()), str(pd.to_datetime(events["exit_date"]).max().date())],
        "data_audit": data,
        "all_trades": {"overall": dg.outcome_stats(g), "loss_profile": dg.loss_profile(g), "by_strategy": by(g, "strategy_id"),
                       "by_regime": by(g, "regime"), "by_score_bucket": by(g, "score_bucket"), "by_year": by(g, "year"),
                       "by_sector": by(g, "sector"), "by_exit_reason": by(g, "exit_reason"),
                       "near_corporate_action": by(g, "near_corporate_action")},
        "published_by_current_gate": {"overall": dg.outcome_stats(pub), "loss_profile": dg.loss_profile(pub),
                                      "by_strategy": by(pub, "strategy_id"), "by_regime": by(pub, "regime"),
                                      "by_score_bucket": by(pub, "score_bucket"), "by_evidence_level": by(pub, "evidence_level"),
                                      "rejected_by": g.loc[~g["published"], "gate_failed"].value_counts().to_dict()},
        "gate_comparison": (cmp := dg.compare_policies(events, policies(cfg))),
        "strategy_table": strategy_table(events, cmp["split"]),
        "gate_comparison_excluding_corporate_actions": dg.compare_policies(events[~events["near_corporate_action"]], policies(cfg)[:1] + policies(cfg)[3:4]),
        "track_record": track_record(db, market, events),
        "notes": [
            "Measured on the stored daily history with the live rules: entry at the next open, stop assumed first when one day touches "
            "stop and target, costs and slippage included, one position per symbol and strategy.",
            "The gate replay uses only trades that had exited before each signal date. It reproduces the evidence, score, R:R, stop-width, "
            "target-distance and regime checks; liquidity, data-quality, volatility and timeframe checks are not replayed.",
            "Survivorship: the NSE pool is today's most-traded stocks; companies that delisted or shrank are missing, which flatters the past.",
        ],
    }
