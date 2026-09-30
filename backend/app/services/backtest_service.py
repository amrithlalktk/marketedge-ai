"""User-requested backtests: summary metrics, train/validation/OOS segments,
walk-forward, parameter sensitivity, Monte Carlo and overfitting warnings."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import pandas as pd
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Backtest, BacktestTrade
from app.services import strategy_service
from app.services.market_data import instrument_maps, load_bars
from app.services.scan_service import _json_safe, _trade_rows
from app.services.settings_service import engine_config
from engine.analyzer import Analyzer
from engine.config import CostModel
from engine.metrics import hit_rates, monte_carlo, summary
from engine.strategies import StrategySpec, build_custom_strategy
from engine.portfolio import PortfolioConfig
from engine.portfolio import simulate as simulate_portfolio
from engine.walkforward import (default_segments, overfit_warnings, parameter_sensitivity, segment_stats, too_good_warnings, walk_forward,
                                yearly_stability)

DEFAULT_GRID = {"atr_stop_mult": [1.5, 2.0, 2.5], "t2_r": [2.5, 3.0, 4.0]}
MAX_STORED_TRADES = 20000


def resolve_strategy(db: Session, params: dict) -> Tuple[StrategySpec, Optional[int]]:
    """Returns (spec, strategy_version_id). Ad-hoc definitions have no stored version."""
    if params.get("definition"):
        return build_custom_strategy(params["definition"]), None
    return strategy_service.resolve(db, params.get("strategy_key"), params.get("strategy_version"))


def to_account_currency(db: Session, market: str, trades: List[dict], closes: dict, info: dict):
    """Convert every instrument's prices into one account currency (INR for NSE, USD elsewhere)
    at the historical daily FX close of each date, so a multi-currency portfolio is summed in one unit."""
    from app.services.fx_service import rate_series

    account = "INR" if market == "NSE" else "USD"
    ccys = {k: ("USD" if (info[k].get("currency") or account) == "USDT" else (info[k].get("currency") or account)) for k in closes}
    series, paths, missing = {}, {}, []
    for c in sorted(set(ccys.values()) - {account}):
        s, p = rate_series(db, c, account)
        if s is None:
            missing.append(c)
        else:
            series[c], paths[c] = s.sort_index(), p
    note = {"currency": account if not missing else "MIXED",
            "fx_conversion": {"applied": bool(series), "account_currency": account, "pairs": paths, "missing": missing,
                              "note": ("Prices converted at the historical daily FX close for each date." if series else "Single-currency market; no conversion.")
                              + (f" No FX series for {', '.join(missing)} — those amounts are NOT converted." if missing else "")}}
    if not series:
        return trades, closes, note

    def at(c, d):
        s = series.get(c)
        if s is None:
            return 1.0
        v = s.asof(pd.Timestamp(d))
        return float(v) if v == v else float(s.iloc[0])

    out = []
    for t in trades:
        c = ccys.get(t["symbol"], account)
        if c not in series:
            out.append(t)
            continue
        re, rx = at(c, t["entry_date"]), at(c, t["exit_date"])
        r1 = at(c, t["t1_date"]) if t.get("t1_date") else re
        out.append({**t, "entry": t["entry"] * re, "stop": t["stop"] * re, "t1": t["t1"] * r1, "exit_price": t["exit_price"] * rx})
    conv = {}
    for k, s in closes.items():
        c = ccys[k]
        conv[k] = s * series[c].reindex(s.index.union(series[c].index)).ffill().reindex(s.index).bfill() if c in series else s
    return out, conv, note


def execute_backtest(db: Session, backtest_id: int) -> Backtest:
    bt_row = db.get(Backtest, backtest_id)
    bt_row.status, bt_row.started_at = "running", datetime.now(timezone.utc)
    db.commit()
    try:
        p = bt_row.params
        from app.core.markets import market_config

        market = p.get("market") or get_settings().market
        mc = market_config(market)
        cfg = engine_config(db, market=market)
        if p.get("costs"):
            cfg.backtest.costs = CostModel(**{**cfg.backtest.costs.__dict__, **p["costs"]})
        cfg.backtest.risk_per_trade_pct = float(p.get("risk_per_trade_pct", cfg.backtest.risk_per_trade_pct))
        cfg.backtest.partial_at_t1 = float(p.get("partial_at_t1", cfg.backtest.partial_at_t1))
        spec, _ = resolve_strategy(db, p)
        if p.get("max_hold_bars"):
            spec = replace(spec, max_hold_bars=int(p["max_hold_bars"]))

        ids, info = instrument_maps(db, market)
        wanted = set(p.get("symbols") or [])
        chosen = {i: sym for i, sym in ids.items() if (sym in wanted if wanted else not info[sym]["is_index"])}
        bench_id = next((i for i, sym in ids.items() if sym == mc.benchmark), None)
        bars = load_bars(db, {**chosen, **({bench_id: mc.benchmark} if bench_id else {})})
        bench = bars[mc.benchmark] if mc.benchmark in chosen.values() else bars.pop(mc.benchmark, None)
        if bench is None:
            raise RuntimeError(f"Benchmark {mc.benchmark} data missing — run ingestion for {market} first")
        analyzer = Analyzer(cfg, workers=get_settings().scan_workers)
        feats = analyzer.prepare({k: v for k, v in bars.items() if k in chosen.values()})
        if not feats:
            raise RuntimeError("No instruments with enough history for this backtest")
        from app.services.universe_service import membership_for

        membership, delisted, universe = membership_for(db, market, info, bars)
        ctx = analyzer.market_context(bench, feats, membership=membership)
        start = p.get("start") or str(min(f.index[0] for f in feats.values()).date())
        end = p.get("end") or str(max(f.index[-1] for f in feats.values()).date())
        dates = {k: (v["listed_on"], v["delisted_on"]) for k, v in info.items()}

        def run(params: dict, a: str, b: str) -> List[dict]:
            base = spec.levels or cfg.levels  # a custom strategy's own exits are the grid's centre
            lv = replace(base, **params) if params else base
            sp = replace(spec, levels=lv) if spec.levels is not None else spec
            ev = analyzer.build_events(feats, ctx.regime_df, [sp], a, b, levels_cfg=lv, universe_dates=dates,
                                       membership=membership, final_symbols=delisted)
            return [t for t in ev.to_dict("records") if t["exit_date"] <= b] if not ev.empty else []

        trades = run({}, start, end)
        segs = segment_stats(trades, default_segments(start, end))
        result = {
            "strategy": spec.public(), "universe_size": len(feats), "period": [start, end],
            **({"point_in_time_universe": universe} if universe else {}),
            "costs": cfg.backtest.costs.__dict__, "risk_per_trade_pct": cfg.backtest.risk_per_trade_pct,
            "partial_at_t1": cfg.backtest.partial_at_t1,
            "market": market,
            "summary": summary(trades, cfg.backtest.risk_per_trade_pct, start, end, mc.profile.periods_per_year),
            "segments": segs,
            "by_regime": {r: hit_rates(g.to_dict("records")) for r, g in pd.DataFrame(trades).groupby("regime")} if trades else {},
            "monte_carlo": monte_carlo(trades, cfg.backtest.risk_per_trade_pct),
            "is_sample_data": any(info[k]["is_sample"] for k in feats),
            "methodology": {
                "entry": spec.entry_rule, "stop": spec.stop_rule, "targets": spec.target_rule,
                "same_bar": "Stop assumed before target when both are touched in one bar.",
                "survivorship": "Instruments are only traded between their listing and delisting dates; delisted instruments remain in the sample.",
                "look_ahead": "Signals use bar-t data only; entries fill at the next bar's open; swing pivots are used only after confirmation.",
            },
        }
        wf, sens = None, None
        if p.get("walk_forward", True):
            grid = p.get("grid") or DEFAULT_GRID
            wf = walk_forward(run, grid, start, end, float(p.get("train_years", 3)), float(p.get("test_years", 1)))
            result["walk_forward"] = {"folds": wf["folds"], "out_of_sample_combined": wf["out_of_sample_combined"], "grid": grid}
            sens = parameter_sensitivity(run, grid, start, end)
            result["sensitivity"] = sens
        stability = yearly_stability(trades)
        result["yearly_stability"] = stability
        if p.get("portfolio") is not None and trades:
            pc = p["portfolio"]
            pcfg = PortfolioConfig(initial_capital=float(pc.get("initial_capital", 1_000_000)), risk_per_trade_pct=cfg.backtest.risk_per_trade_pct,
                                   max_positions=int(pc.get("max_positions", 10)), max_position_pct=float(pc.get("max_position_pct", 20)),
                                   max_sector_pct=pc.get("max_sector_pct"), partial_at_t1=cfg.backtest.partial_at_t1,
                                   commission_pct=cfg.backtest.costs.commission_pct, slippage_pct=cfg.backtest.costs.slippage_pct,
                                   periods_per_year=mc.profile.periods_per_year)
            p_trades, p_closes, fxnote = to_account_currency(db, market, trades, {k: v["close"] for k, v in feats.items()}, info)
            result["portfolio"] = simulate_portfolio(p_trades, p_closes, pcfg, {k: v["sector"] for k, v in info.items() if v["sector"]}, bench["close"])
            result["portfolio"].update(fxnote)
        result["warnings"] = overfit_warnings(segs, wf, sens) + too_good_warnings(result["summary"], stability)
        if result["is_sample_data"]:
            result["warnings"].insert(0, "Backtest ran on SAMPLE/SYNTHETIC data — results say nothing about real markets.")

        rows = _trade_rows(pd.DataFrame(trades[:MAX_STORED_TRADES]), bt_row.id)
        for i in range(0, len(rows), 5000):
            db.execute(insert(BacktestTrade), rows[i : i + 5000])
        bt_row.result = _json_safe(result)
        bt_row.status, bt_row.finished_at = "done", datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        bt_row = db.get(Backtest, backtest_id)
        bt_row.status, bt_row.error, bt_row.finished_at = "failed", str(exc)[:2000], datetime.now(timezone.utc)
        db.commit()
    return bt_row
