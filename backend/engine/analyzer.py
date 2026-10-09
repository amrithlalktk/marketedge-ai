"""Analysis engine orchestrator.

Answers, for a universe of instruments:
  1. Is the market favorable?           -> market_context()
  2-6. Which assets, direction, entry, stop, targets? -> evaluate_symbol()
  7-8. Historical probability & sample   -> build_events() + probability.estimate()
  9. What could invalidate it?           -> explain()
 10. Should we say NO TRADE?             -> validation.validate()
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from . import ENGINE_VERSION, mtf as mtf_mod
from .backtest import backtest_symbol
from .breadth import breadth_series, breadth_snapshot, sector_rotation
from .config import EngineConfig, LevelConfig
from .explain import explain
from .features import build_features
from .levels import TradeLevels, compute_levels, price_round
from .metrics import summary
from .probability import estimate, score_bucket, similar_examples
from .regime import classify, describe
from .scoring import combine, compute_score, score_historical
from .strategies import STRATEGIES, StrategySpec, detect
from .validation import Check, validate, verdict
from .walkforward import default_segments, overfit_warnings, segment_stats

DISCLAIMER = ("Historical/backtested performance and probability estimates do not guarantee future results. "
              "Market conditions can change rapidly. This platform provides analytical information and does not guarantee profits.")


@dataclass
class MarketContext:
    regime_df: pd.DataFrame
    regime_now: Optional[dict]
    breadth_df: pd.DataFrame
    breadth_now: dict


def _room_r(levels: TradeLevels) -> Optional[float]:
    ahead = levels.resistances if levels.direction == "LONG" else levels.supports
    if not ahead:
        return None
    return abs(ahead[0] - levels.reference_price) / levels.risk_per_unit


def _label(score: float, good: str, ok: str, bad: str) -> str:
    return good if score >= 75 else ok if score >= 50 else bad


def _prep_one(sym, data):
    return build_features(data["bars"][sym])


def _events_one(sym, data):
    return data["analyzer"]._events_for(sym, data["features"][sym], data["reg"], data["strategies"], data["lv"], data["lo_hi"][sym],
                                        (data.get("membership") or {}).get(sym, None) if data.get("membership") is not None else None,
                                        sym in data.get("final", ()))


class Analyzer:
    def __init__(self, cfg: Optional[EngineConfig] = None, workers: int = 1):
        self.cfg = cfg or EngineConfig()
        self.workers = workers  # >1 → per-symbol process parallelism (engine.parallel); output is identical to serial

    # ---------------------------------------------------------------- features
    def prepare(self, bars: Dict[str, pd.DataFrame], min_bars: int = 60) -> Dict[str, pd.DataFrame]:
        from .parallel import map_keys

        keys = [sym for sym, df in bars.items() if df is not None and len(df) >= min_bars]
        return dict(zip(keys, map_keys(_prep_one, keys, {"bars": bars}, self.workers)))

    # ---------------------------------------------------------------- market
    def market_context(self, benchmark: pd.DataFrame, features: Dict[str, pd.DataFrame], vix: Optional[pd.Series] = None,
                       membership: Optional[Dict[str, pd.Series]] = None) -> MarketContext:
        b = breadth_series(features, membership)
        reg = classify(benchmark, vix=vix, breadth50=b["pct_above_50"] if not b.empty else None)
        now = describe(reg, benchmark["close"]) if reg["regime"].notna().any() else None
        return MarketContext(reg, now, b, breadth_snapshot(b) if not b.empty else {})

    # ---------------------------------------------------------------- history
    def _signal_context(self, f: pd.DataFrame, spec: StrategySpec, reg: pd.DataFrame, fundamentals: Optional[dict]):
        def ctx(t: int, levels: TradeLevels) -> dict:
            ts = f.index[t]
            r = reg["regime"].get(ts) if ts in reg.index else None
            v = reg["volatility"].get(ts) if ts in reg.index else None
            sc, _, _ = compute_score(f.iloc[t], spec.direction, levels.rr_t2, _room_r(levels), r, v, None, self.cfg)
            return {"regime": r, "regime_family": reg["family"].get(ts) if ts in reg.index else None,
                    "score_at_signal": sc, "score_bucket": score_bucket(sc)}
        return ctx

    def build_events(self, features: Dict[str, pd.DataFrame], reg: pd.DataFrame, strategies: Optional[List[StrategySpec]] = None,
                     start: Optional[str] = None, end: Optional[str] = None, levels_cfg: Optional[LevelConfig] = None,
                     universe_dates: Optional[Dict[str, tuple]] = None, membership: Optional[Dict[str, pd.Series]] = None,
                     final_symbols: Optional[set] = None) -> pd.DataFrame:
        """Replay each strategy on every instrument. `universe_dates` maps symbol ->
        (listed_from, delisted_on) so trades only occur while the instrument was
        actually in the universe (point-in-time membership). `membership` (engine.universe)
        additionally restricts SIGNALS to dates the symbol was a top-N member; `final_symbols`
        are delisted series whose open trades are closed at the last bar instead of excluded."""
        from .parallel import map_keys

        strategies = strategies or list(STRATEGIES.values())
        lv = levels_cfg or self.cfg.levels
        s_ts = pd.Timestamp(start) if start else None
        e_ts = pd.Timestamp(end) if end else None
        lo_hi = {}
        for sym in features:
            lo, hi = s_ts, e_ts
            if universe_dates and sym in universe_dates:
                a, b = universe_dates[sym]
                lo = max(filter(None, [lo, pd.Timestamp(a) if a else None]), default=None)
                hi = min(filter(None, [hi, pd.Timestamp(b) if b else None]), default=None)
            lo_hi[sym] = (lo, hi)
        keys = list(features)
        if membership is not None:
            keys = [k for k in keys if k in membership and membership[k].any()]  # never a member → no signals at all
        parts = map_keys(_events_one, keys, {"analyzer": self, "features": features, "reg": reg, "strategies": strategies, "lv": lv, "lo_hi": lo_hi,
                                             "membership": membership, "final": set(final_symbols or ())}, self.workers)
        return pd.DataFrame([row for part in parts for row in part])

    def _events_for(self, sym: str, f: pd.DataFrame, reg: pd.DataFrame, strategies: List[StrategySpec], lv: LevelConfig, lo_hi,
                    member: Optional[pd.Series] = None, data_final: bool = False) -> List[dict]:
        lo, hi = lo_hi
        rows: List[dict] = []
        m = member.reindex(f.index, fill_value=False).astype(bool) if member is not None else None
        for spec in strategies:
            sig = detect(spec, f)
            if m is not None:
                sig = sig & m  # filtered BEFORE simulation so one-position-at-a-time logic stays correct
            rows.extend(backtest_symbol(f, sym, spec, sig, self.cfg.backtest, lv, lo, hi, self._signal_context(f, spec, reg, None),
                                        data_final=data_final))
        return rows

    def strategy_performance(self, events: pd.DataFrame, strategy: StrategySpec, market: str, periods_per_year: int = 252) -> dict:
        e = events[events["strategy_id"] == strategy.id] if not events.empty else events
        trades = e.to_dict("records")
        if not trades:
            return {"strategy": strategy.public(), "market": market, "summary": {"sample_size": 0}, "segments": {}, "warnings": ["No historical trades"]}
        start, end = str(pd.to_datetime(e["signal_date"]).min().date()), str(pd.to_datetime(e["exit_date"]).max().date())
        segs = segment_stats(trades, default_segments(start, end))
        by_regime = {}
        if "regime" in e.columns:
            from .metrics import hit_rates
            for r, g in e.groupby(e["regime"].fillna("Unknown")):
                by_regime[r] = hit_rates(g.to_dict("records"))
        return {
            "strategy": strategy.public(), "market": market, "backtest_period": [start, end],
            "summary": summary(trades, self.cfg.backtest.risk_per_trade_pct, start, end, periods_per_year),
            "segments": segs, "by_regime": by_regime, "warnings": overfit_warnings(segs),
            "costs": {"commission_pct_per_side": self.cfg.backtest.costs.commission_pct, "slippage_pct_per_side": self.cfg.backtest.costs.slippage_pct},
        }

    # ---------------------------------------------------------------- live
    def evaluate_symbol(self, symbol: str, f: pd.DataFrame, events: pd.DataFrame, ctx: MarketContext, *, today: date,
                        instrument: Optional[dict] = None, fundamentals: Optional[dict] = None, earnings_in_days: Optional[int] = None,
                        data_meta: Optional[dict] = None, intraday_1h: Optional[pd.DataFrame] = None,
                        strategies: Optional[List[StrategySpec]] = None, market: str = "NSE", only_active: bool = True,
                        calendar: str = "weekdays", liquidity_mult: float = 1.0, liquidity_currency: str = "INR",
                        short_note: Optional[str] = None, acceptance: Optional[Dict[str, dict]] = None,
                        calibration: Optional[dict] = None) -> List[dict]:
        """`acceptance` (engine.acceptance.evaluate): when given, a strategy that is not validated out of sample adds a
        blocking check — its setups can be paper trades but never published ideas. `calibration` is attached to the
        probability so the shown chance carries its measured accuracy."""
        strategies = strategies or list(STRATEGIES.values())
        instrument = instrument or {}
        t = len(f) - 1
        row = f.iloc[t]
        as_of = str(f.index[t].date())
        results = []
        for spec in strategies:
            fired = bool(detect(spec, f).iloc[t])
            if only_active and not fired:
                continue
            levels = compute_levels(f, t, spec.direction, spec.levels or self.cfg.levels)
            if levels is None:
                continue
            reg = ctx.regime_now
            score, comps, notes = compute_score(row, spec.direction, levels.rr_t2, _room_r(levels), reg and reg["regime"], reg and reg["volatility"], fundamentals, self.cfg)
            # probability conditioning uses the same technical-only score used when labelling history
            tech_score, _, _ = compute_score(row, spec.direction, levels.rr_t2, _room_r(levels), reg and reg["regime"], reg and reg["volatility"], None, self.cfg)
            prob = estimate(events, spec.id, as_of, reg and reg["family"], score_bucket(tech_score),
                            self.cfg.validation.min_sample_size, market, spec.timeframe)
            comps["historical"] = score_historical(prob, self.cfg.validation.min_sample_size)
            if self.cfg.weights.get("historical", 0) > 0 and comps["historical"] is not None:
                score = combine(comps, {**self.cfg.weights} if self.cfg.analysis_mode != "technical" else {**self.cfg.weights, "fundamental": 0})
            m = mtf_mod.analyze(f[["open", "high", "low", "close", "volume"]], spec.direction, intraday_1h)
            checks = validate(f=f, direction=spec.direction, levels=levels, score=score, prob=prob, regime=reg, mtf=m,
                              today=today, cfg=self.cfg.validation, earnings_in_days=earnings_in_days, data_meta=data_meta,
                              is_index=bool(instrument.get("is_index") or instrument.get("volumeless")), calendar=calendar,
                              liquidity_mult=liquidity_mult, liquidity_currency=liquidity_currency)
            validation = None
            if acceptance is not None:
                from .acceptance import VALIDATED, as_check_detail

                validation = acceptance.get(spec.id)
                checks.append(Check("Strategy validation (out-of-sample)", bool(validation and validation["status"] == VALIDATED), "block",
                                    as_check_detail(validation)))
                prob = {**prob, "out_of_sample": (validation or {}).get("out_of_sample"), "validation_status": (validation or {}).get("status", "UNVALIDATED")}
            if calibration is not None:
                prob = {**prob, "calibration": calibration}
            status = verdict(checks) if fired else "NO_SIGNAL"
            sign = 1 if spec.direction == "LONG" else -1
            ref = levels.reference_price
            results.append({
                "symbol": symbol,
                "name": instrument.get("name", symbol),
                "market": market,
                "exchange": instrument.get("exchange", market),
                "currency": instrument.get("currency", "INR"),
                "sector": instrument.get("sector"),
                "timeframe": spec.timeframe,
                "as_of": as_of,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "engine_version": ENGINE_VERSION,
                "status": status,
                "strategy": {**spec.public(), **({"notes": short_note} if short_note is not None and spec.direction == "SHORT" and spec.id in STRATEGIES else {})},
                "setup_type": spec.setup_type,
                "direction": spec.direction,
                "current_price": price_round(float(row["close"]), float(row["close"])),
                "entry_zone": [levels.entry_low, levels.entry_high],
                "entry_method": spec.entry_rule,
                "stop": levels.stop,
                "stop_method": levels.stop_method,
                "targets": levels.targets,
                "target_methods": levels.target_methods,
                "risk_pct": round(100 * levels.risk_per_unit / ref, 2),
                "reward_pct_t1": round(100 * sign * (levels.targets[0] - ref) / ref, 2),
                "reward_pct_t2": round(100 * sign * (levels.targets[1] - ref) / ref, 2),
                "rr_t1": levels.rr_t1,
                "rr_t2": levels.rr_t2,
                "atr": levels.atr,
                "score": score,
                "score_label": self.cfg.label_for(score),
                "components": comps,
                "score_notes": notes,
                "analysis_mode": self.cfg.analysis_mode,
                "trend": _label(comps["trend"], "Strong " + ("Uptrend" if sign == 1 else "Downtrend"), "Moderate trend", "Weak/against"),
                "momentum": _label(comps["momentum"], "Positive" if sign == 1 else "Negative (bearish)", "Mixed", "Against direction"),
                "volume": _label(comps["volume"], "Strong", "Average", "Weak") if comps["volume"] is not None else "n/a (no volume)",
                "market_regime": reg,
                "mtf": m,
                "probability": prob,
                "validation": validation,
                "expected_holding_days": prob.get("avg_holding_bars"),
                "historical_examples": similar_examples(events, spec.id, as_of, symbol, 8),
                "checks": [c.to_dict() for c in checks],
                "explanation": explain(row, spec.direction, levels, prob, reg, m, checks, spec),
                "data": data_meta or {},
                "indicators": {k: (None if pd.isna(row.get(k)) else round(float(row.get(k)), 3)) for k in
                               ("rsi", "adx", "macd_hist", "atr", "atr_pct", "vol_ratio20", "vol_ratio50", "ema20", "ema50", "ema200", "mfi", "cci", "stoch_k", "bb_pctb", "supertrend")},
                "disclaimer": DISCLAIMER,
            })
        return results

    def scan(self, features: Dict[str, pd.DataFrame], events: pd.DataFrame, ctx: MarketContext, *, today: date,
             instruments: Dict[str, dict], meta: Dict[str, dict], market: str = "NSE",
             fundamentals: Optional[Dict[str, dict]] = None, earnings: Optional[Dict[str, int]] = None,
             strategies: Optional[List[StrategySpec]] = None, eval_kwargs: Optional[Dict] = None,
             liquidity_mults: Optional[Dict[str, float]] = None) -> Dict:
        setups = []
        for sym, f in features.items():
            inst = instruments.get(sym, {})
            if inst.get("is_index") or inst.get("delisted_on"):
                continue
            setups.extend(self.evaluate_symbol(sym, f, events, ctx, today=today, instrument=inst,
                                               fundamentals=(fundamentals or {}).get(sym), earnings_in_days=(earnings or {}).get(sym),
                                               data_meta=meta.get(sym), market=market, strategies=strategies,
                                               liquidity_mult=(liquidity_mults or {}).get(sym, 1.0), **(eval_kwargs or {})))
        valid = sorted([s for s in setups if s["status"] == "VALID"], key=lambda s: -s["score"])
        rejected = [s for s in setups if s["status"] == "NO_TRADE"]
        market_msg = None
        if ctx.regime_now and ctx.regime_now["regime"] == "Panic/Selloff":
            market_msg = "NO TRADE: market in Panic/Selloff regime — long setups are blocked."
        elif not valid:
            market_msg = "NO VALID SETUP: no candidate passed every validation check today."
        return {"as_of": max((str(f.index[-1].date()) for f in features.values()), default=None),
                "valid": valid, "no_trade": rejected, "market_message": market_msg,
                "candidates_evaluated": len(setups), "instruments_scanned": len(features)}

    def sectors(self, features: Dict[str, pd.DataFrame], sectors: Dict[str, str], benchmark: pd.Series) -> list:
        return sector_rotation(features, sectors, benchmark)
