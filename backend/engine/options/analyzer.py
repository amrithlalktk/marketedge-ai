"""Options analysis orchestrator: one call produces the full NIFTY options view."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import Dict, List, Optional

import pandas as pd

from ..analyzer import DISCLAIMER, Analyzer, MarketContext
from ..config import INDEX_LEVELS
from ..strategies import INDEX_STRATEGIES
from . import chain as ch
from .selector import OptionsConfig, find_option_setups
from .state import classify_state, intraday_context
from .strategies import build_strategies

OPTIONS_DISCLAIMER = (DISCLAIMER + " Options can lose their entire premium quickly; short options carry unlimited risk. "
                      "Option levels are model estimates (Black-76, constant IV).")


def index_analyzer(analyzer: Analyzer) -> Analyzer:
    """Same thresholds/weights, index-horizon level geometry."""
    return Analyzer(replace(analyzer.cfg, levels=INDEX_LEVELS))


def _failed(checks) -> list:
    return [c["name"] for c in checks or [] if not c["passed"] and c["severity"] == "block"]


def no_trade_reason(underlying_setups: list, option_setups: list) -> str:
    """Why there is no NIFTY option idea today, in one sentence."""
    if not underlying_setups:
        return "NO TRADE: none of the NIFTY strategies triggered on the latest index candle (no option idea without one)."
    valid = [u for u in underlying_setups if u["status"] == "VALID"]
    if not valid:
        parts = [f"{u['strategy']['name']} ({u['direction'].lower()}) failed {', '.join(_failed(u.get('checks'))[:3]) or 'its checks'}"
                 for u in underlying_setups[:2]]
        return "NO TRADE: a NIFTY setup triggered but did not pass: " + "; ".join(parts) + "."
    parts = []
    for o in option_setups[:2]:
        why = o.get("reason") or ", ".join(n.removeprefix("Underlying: ") for n in _failed(o.get("checks"))[:3]) or "its checks"
        parts.append(f"{(o.get('contract') or {}).get('label', o.get('direction', 'contract'))}: {why}")
    return "NO TRADE: the NIFTY setup passed, but no option contract passed its checks (" + "; ".join(parts) + ")."


def index_events(analyzer: Analyzer, index_features: pd.DataFrame, symbol: str, regime_df: pd.DataFrame) -> pd.DataFrame:
    return index_analyzer(analyzer).build_events({symbol: index_features}, regime_df, list(INDEX_STRATEGIES.values()))


def analyze_options(*, analyzer: Analyzer, symbol: str, display_name: str, index_features: pd.DataFrame, events: pd.DataFrame,
                    ctx: MarketContext, chain_raw: pd.DataFrame, spot: float, as_of: datetime, lot_size: int,
                    iv_history: Optional[pd.Series], intraday_5m: Optional[pd.DataFrame], today: date,
                    chain_meta: dict, prev_ltp: Optional[pd.Series] = None,
                    chain_cfg: Optional[ch.ChainConfig] = None, opt_cfg: Optional[OptionsConfig] = None,
                    macro_events: Optional[List[dict]] = None) -> Dict:
    """`macro_events`: economic releases relevant to the underlying (e.g. IN + US for NIFTY)."""
    chain_cfg = chain_cfg or ch.ChainConfig()
    opt_cfg = opt_cfg or OptionsConfig()
    c = ch.enrich(chain_raw, spot, as_of, chain_cfg, lot_size)
    expiries = sorted(c["expiry"].unique())
    tradable = [e for e in expiries if (e - as_of.date()).days >= 1] or expiries
    near = tradable[0]
    atm_by_exp = {e: ch.atm_iv(c, e) for e in expiries}
    atm_near = atm_by_exp.get(near)
    ivp = ch.iv_percentile(iv_history, atm_near) if iv_history is not None and atm_near else None
    intraday = intraday_context(intraday_5m)
    state = classify_state(index_features, ctx.regime_now, ivp, intraday)

    fut_T = float(c[c["expiry"] == expiries[-1]]["T"].iloc[0]) if len(expiries) else 0.0
    futures = {"near_expiry": str(near), "forward": round(float(c[c["expiry"] == near]["forward"].iloc[0]), 2),
               "basis_pts": round(float(c[c["expiry"] == near]["forward"].iloc[0] - spot), 2),
               "note": "Theoretical forward F = S·e^{(r−q)T}; replace with traded futures price when a futures feed is configured."}
    _ = fut_T
    straddle = ch.straddle_price(c, near)
    days_near = max((near - as_of.date()).days, 1)
    oi = ch.oi_levels(c, near, spot)

    und = index_analyzer(analyzer).evaluate_symbol(symbol, index_features, events, ctx, today=today,
                                   instrument={"name": display_name, "exchange": "NSE", "currency": "INR", "is_index": True},
                                   data_meta=chain_meta, strategies=list(INDEX_STRATEGIES.values()), market="NFO")
    setups = find_option_setups(c, spot, und, ivp, atm_near, lot_size, chain_cfg, opt_cfg, display_name, chain_meta)

    reg_df = ctx.regime_df
    regime_mask = (reg_df["family"] == ctx.regime_now["family"]) if ctx.regime_now is not None and not reg_df.empty else None
    trading_days = pd.bdate_range(as_of.date(), max(expiries)) if expiries else []
    sessions_to = {e: max(int(((trading_days > pd.Timestamp(as_of.date())) & (trading_days <= pd.Timestamp(e))).sum()), 1) for e in expiries}
    macro_events = macro_events or []  # no economic calendar in the lite build: event gating is a no-op
    high_before = {}
    for e in expiries:
        exp_end = pd.Timestamp(datetime.combine(e, datetime.min.time()).replace(hour=10), tz="UTC")  # 15:30 IST
        names = [f"{m['name']} ({pd.Timestamp(m['event_time']).tz_convert('Asia/Kolkata').strftime('%d %b')})" for m in
                 sorted(macro_events, key=lambda x: x["event_time"]) if m.get("impact") == "High"
                 and pd.Timestamp(as_of) <= pd.Timestamp(m["event_time"]) <= exp_end]
        high_before[e] = list(dict.fromkeys(names))
    strategies = build_strategies(c, spot, state, ivp, atm_by_exp, ctx.regime_now, oi, und, lot_size, chain_cfg,
                                  index_features["close"], regime_mask, sessions_to, high_before)

    def chain_rows(e):
        sub = c[c["expiry"] == e]
        rows = []
        for k in sorted(sub["strike"].unique()):
            r = {"strike": float(k)}
            for t in ("CE", "PE"):
                x = sub[(sub["strike"] == k) & (sub["option_type"] == t)]
                if x.empty:
                    continue
                x = x.iloc[0]
                r[t] = {kk: (None if pd.isna(x[kk]) else round(float(x[kk]), 4 if kk in ("delta", "gamma") else 2))
                        for kk in ("bid", "ask", "ltp", "mid", "spread_pct", "volume", "oi", "oi_change", "iv", "delta", "gamma", "theta", "vega")}
                r[t]["iv"] = None if r[t]["iv"] is None else round(100 * r[t]["iv"], 2)
                r[t]["liquid"] = bool(x["liquid"])
            rows.append(r)
        return rows

    status = "VALID" if any(s["status"] == "VALID" for s in setups) else "NO_TRADE"
    return {
        "underlying": {"symbol": symbol, "name": display_name, "spot": round(spot, 2), "as_of": as_of.isoformat(),
                       "lot_size": lot_size, "futures": futures},
        "market_state": state,
        "intraday": intraday,
        "regime": ctx.regime_now,
        "iv": {"atm_iv_near_pct": None if atm_near is None else round(100 * atm_near, 2), "iv_percentile": ivp,
               "iv_percentile_basis": "trailing 252 observations of ATM IV" if ivp is not None else "unavailable (insufficient IV history)",
               "skew_25d_pts": ch.skew_25d(c, near),
               "term_structure": [{"expiry": str(e), "dte": int((e - as_of.date()).days), "atm_iv_pct": None if atm_by_exp[e] is None else round(100 * atm_by_exp[e], 2)} for e in expiries]},
        "expected_move": {"expiry": str(near), "straddle_price": None if straddle is None else round(straddle, 2),
                          "iv_1sd_to_expiry": None if atm_near is None else round(ch.expected_move(spot, atm_near, days_near), 1),
                          "iv_1sd_1day": None if atm_near is None else round(ch.expected_move(spot, atm_near, 1), 1)},
        "oi": {"expiry": str(near), **ch.pcr(c, near), "pcr_all_expiries": ch.pcr(c)["pcr_oi"], "max_pain": ch.max_pain(c, near), **oi,
               "buildup": ch.oi_buildup(c, near, prev_ltp)[:30],
               "profile": [{"strike": float(k), "call_oi": float(g[g["is_call"]]["oi"].sum()), "put_oi": float(g[~g["is_call"]]["oi"].sum()),
                            "call_oi_change": float(g[g["is_call"]]["oi_change"].sum()), "put_oi_change": float(g[~g["is_call"]]["oi_change"].sum())}
                           for k, g in c[(c["expiry"] == near) & (c["strike"].between(spot * 0.95, spot * 1.05))].groupby("strike")],
               "note": "OI levels show where option writers are positioned; they are not guaranteed support/resistance."},
        "liquidity": {"liquid_contracts": int(c["liquid"].sum()), "total_contracts": int(len(c)), "filters": {
            "max_spread_pct": chain_cfg.max_spread_pct, "min_oi_lots": chain_cfg.min_oi_lots, "min_volume_lots": chain_cfg.min_volume_lots, "min_premium": chain_cfg.min_premium}},
        "expiries": [str(e) for e in expiries],
        "chain": {str(e): chain_rows(e) for e in expiries[:3]},
        "underlying_setups": und,
        "option_setups": setups,
        "strategies": strategies,
        "status": status,
        "market_message": None if status == "VALID" else no_trade_reason(und, setups),
        "events": {"upcoming": [], "high_impact_before_expiry": {str(k): v for k, v in high_before.items() if v},
                   "note": "No economic calendar configured: macro-event risk before expiry is not checked."},
        "config": {"chain": chain_cfg.__dict__, "selector": opt_cfg.to_dict()},
        "data": chain_meta,
        "disclaimer": OPTIONS_DISCLAIMER,
    }
