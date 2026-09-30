"""Options strategy engine.

Every strategy has explicit eligibility conditions (direction, IV regime,
market regime, liquidity). Only eligible strategies are proposed; the rest are
returned with the conditions they failed, so the choice is explainable.

For each proposed structure:
* payoff at expiry → max profit / max loss (flags unlimited), breakevens
* required capital (debit, defined-risk margin, or an INDICATIVE naked margin)
* net Greeks per position
* probability of profit, two ways, both labelled:
    - model: risk-neutral lognormal at expiry using ATM IV (not a real-world forecast)
    - historical: the payoff applied to every historical NIFTY move over the same
      number of sessions (overlapping windows; sample size disclosed)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import erf, sqrt
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .chain import ChainConfig


@dataclass
class Leg:
    option_type: str  # CE | PE
    strike: float
    expiry: str
    side: int         # +1 buy, -1 sell
    lots: int
    premium: float    # fill assumption: buy at ask, sell at bid
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float

    def to_dict(self):
        d = asdict(self)
        d["action"] = "BUY" if self.side > 0 else "SELL"
        d["iv"] = round(100 * self.iv, 2)  # percent, consistent with the chain endpoint
        return d


def leg_from_row(row: pd.Series, side: int, lots: int = 1) -> Leg:
    px = float(row["ask"] if side > 0 else row["bid"]) or float(row["mid"])
    return Leg(row["option_type"], float(row["strike"]), str(row["expiry"]), side, lots, round(px, 2), float(row["iv"]),
               float(row["delta"]), float(row["gamma"]), float(row["theta"]), float(row["vega"]))


def payoff_at_expiry(legs: List[Leg], S: np.ndarray, lot_size: int) -> np.ndarray:
    total = np.zeros_like(S, dtype=float)
    for lg in legs:
        intrinsic = np.maximum(S - lg.strike, 0) if lg.option_type == "CE" else np.maximum(lg.strike - S, 0)
        total += lg.side * lg.lots * lot_size * (intrinsic - lg.premium)
    return total


def _ncdf(x):
    return 0.5 * (1 + np.vectorize(erf)(np.asarray(x) / sqrt(2)))


def analyze_structure(legs: List[Leg], spot: float, forward_price: float, T: float, atm_iv: float, lot_size: int,
                      hist_closes: Optional[pd.Series], sessions_to_expiry: int, regime_mask: Optional[pd.Series] = None) -> Dict:
    strikes = sorted({lg.strike for lg in legs})
    grid = np.unique(np.concatenate([np.linspace(0.01, spot * 0.5, 50), np.linspace(spot * 0.5, spot * 1.6, 2201), np.array(strikes)]))
    pay = payoff_at_expiry(legs, grid, lot_size)
    right_slope = pay[-1] - pay[-2]
    max_profit = None if right_slope > 1e-6 else float(pay.max())
    max_loss = None if right_slope < -1e-6 else float(pay.min())
    if max_profit is not None:
        max_profit = float(max(pay.max(), 0))
    sign = np.sign(pay)
    idx = np.where(np.diff(sign) != 0)[0]
    breakevens = []
    for i in idx:
        x0, x1, y0, y1 = grid[i], grid[i + 1], pay[i], pay[i + 1]
        breakevens.append(round(float(x0 - y0 * (x1 - x0) / (y1 - y0)) if y1 != y0 else float(x0), 2))
    net_premium = sum(-lg.side * lg.lots * lot_size * lg.premium for lg in legs)  # + = credit received

    # model POP: risk-neutral lognormal around the forward
    pop_model = None
    if atm_iv and T > 0:
        sig = atm_iv * sqrt(T)
        mids = (grid[1:] + grid[:-1]) / 2
        z_hi = (np.log(grid[1:] / forward_price) + 0.5 * sig ** 2) / sig
        z_lo = (np.log(grid[:-1] / forward_price) + 0.5 * sig ** 2) / sig
        prob = _ncdf(z_hi) - _ncdf(z_lo)
        pay_mid = payoff_at_expiry(legs, mids, lot_size)
        pop_model = round(100 * float(prob[pay_mid > 0].sum()), 1)
        ev_model = float((prob * pay_mid).sum())
    else:
        ev_model = None

    hist = None
    if hist_closes is not None and len(hist_closes) > sessions_to_expiry + 50:
        h = max(int(sessions_to_expiry), 1)
        logret = np.log(hist_closes / hist_closes.shift(h)).dropna()
        sets = {"all": logret}
        if regime_mask is not None:
            start_mask = regime_mask.astype(float).reindex(hist_closes.index).shift(h).reindex(logret.index).fillna(0).astype(bool)
            sets["same_regime"] = logret[start_mask]
        hist = {"horizon_sessions": h, "note": "Overlapping windows: observations are not independent."}
        for key, lr in sets.items():
            if len(lr) < 30:
                hist[key] = {"sample_size": int(len(lr)), "pop": None}
                continue
            outcomes = payoff_at_expiry(legs, spot * np.exp(lr.to_numpy()), lot_size)
            hist[key] = {"sample_size": int(len(lr)), "pop": round(100 * float((outcomes > 0).mean()), 1),
                         "avg_pnl": round(float(outcomes.mean()), 2), "p5_pnl": round(float(np.percentile(outcomes, 5)), 2),
                         "period": [str(lr.index[0].date()), str(lr.index[-1].date())]}
    g = {k: round(float(sum(lg.side * lg.lots * lot_size * getattr(lg, k) for lg in legs)), 4) for k in ("delta", "gamma", "theta", "vega")}
    return {
        "max_profit": None if max_profit is None else round(max_profit, 2),
        "max_profit_unlimited": max_profit is None,
        "max_loss": None if max_loss is None else round(max_loss, 2),
        "max_loss_unlimited": max_loss is None,
        "breakevens": breakevens,
        "net_premium": round(float(net_premium), 2),
        "premium_type": "credit" if net_premium > 0 else "debit",
        "pop_model": pop_model, "ev_model": None if ev_model is None else round(ev_model, 2),
        "pop_model_note": "Risk-neutral lognormal at expiry using ATM IV — a model estimate, not a forecast.",
        "pop_historical": hist,
        "net_greeks": g,
        "payoff_curve": _curve(grid, pay, spot, strikes, breakevens),
    }


def _curve(grid, pay, spot, strikes, breakevens, every: int = 10) -> list:
    """Downsampled payoff curve that keeps every kink (strikes) and zero crossing (breakevens)."""
    win = (grid >= spot * 0.85) & (grid <= spot * 1.15)
    xs, ys = grid[win], pay[win]
    keep = np.zeros(len(xs), dtype=bool)
    keep[::every] = True
    keep[-1] = True
    for k in strikes:
        keep[np.argmin(np.abs(xs - k))] = True
    pts = [(float(x), float(y)) for x, y in zip(xs[keep], ys[keep])]
    pts += [(float(b), 0.0) for b in breakevens if xs[0] <= b <= xs[-1]]  # exact zero crossings
    return [[round(x, 1), round(y, 1)] for x, y in sorted(pts)]


def capital_and_reward(a: Dict, legs: List[Leg], forward_price: float, lot_size: int) -> Dict:
    """Required capital, its basis, and reward:risk for any analysed structure."""
    if a["premium_type"] == "debit":
        capital, note = -a["net_premium"], "Net debit paid (maximum loss)"
    elif not a["max_loss_unlimited"]:
        capital, note = (-a["max_loss"] if a["max_loss"] is not None else None), "Defined-risk margin ≈ spread width × lot − credit (broker margin may differ)"
    else:
        capital, note = indicative_margin(legs, forward_price, lot_size), "INDICATIVE margin for naked short options (approx. SPAN + exposure); confirm with your broker"
    reward = a["max_profit"]
    risk = None if a["max_loss_unlimited"] else -a["max_loss"]
    uncovered = a["max_loss_unlimited"]
    return {"required_capital": None if capital is None else round(capital, 2), "capital_note": note,
            "reward_to_risk": round(reward / risk, 2) if reward and risk else None,
            "risk_note": "UNLIMITED RISK: at least one short option is uncovered." if uncovered else ""}


def indicative_margin(legs: List[Leg], forward_price: float, lot_size: int, pct: float = 0.12) -> float:
    """Rough SPAN+exposure proxy for NAKED short legs; brokers' actual margin will differ."""
    per_leg = []
    for lg in legs:
        if lg.side < 0:
            otm = max(lg.strike - forward_price, 0) if lg.option_type == "CE" else max(forward_price - lg.strike, 0)
            per_leg.append(max(pct * forward_price - 0.5 * otm, 0.05 * forward_price) * lot_size * lg.lots + lg.premium * lot_size * lg.lots)
    if not per_leg:
        return 0.0
    return float(max(per_leg) + (sum(per_leg) - max(per_leg)) * 0.25)  # offsetting-legs benefit (approximate)


# --------------------------------------------------------------------------- construction helpers
def _pick(chain: pd.DataFrame, expiry, opt: str, target_delta: Optional[float] = None, strike: Optional[float] = None) -> Optional[pd.Series]:
    e = chain[(chain["expiry"] == expiry) & (chain["option_type"] == opt) & chain["iv"].notna()]
    if e.empty:
        return None
    if strike is not None:
        return e.iloc[(e["strike"] - strike).abs().argmin()]
    return e.iloc[(e["delta"].abs() - abs(target_delta)).abs().argmin()]


def _expiry(chain: pd.DataFrame, lo: int, hi: int):
    ex = sorted(chain.loc[chain["dte"].between(lo, hi), "expiry"].unique())
    return ex[0] if ex else None


def _strike_step(chain: pd.DataFrame) -> float:
    s = np.sort(chain["strike"].unique())
    return float(np.median(np.diff(s))) if len(s) > 1 else 50.0


@dataclass
class StrategyTemplate:
    key: str
    name: str
    category: str
    outlook: str
    iv_condition: str
    expiry_rule: str
    management: str
    risk_note: str = ""


TEMPLATES = {t.key: t for t in [
    StrategyTemplate("long_call", "Long Call", "Directional", "Bullish", "IV percentile < 70 (premium not expensive)", "Nearest weekly with ≥ 3 days",
                     "Exit at modelled T1/T2 premium or if underlying closes below the setup stop; do not hold into expiry day."),
    StrategyTemplate("long_put", "Long Put", "Directional", "Bearish", "IV percentile < 70", "Nearest weekly with ≥ 3 days",
                     "Exit at modelled T1/T2 premium or if underlying closes above the setup stop; do not hold into expiry day."),
    StrategyTemplate("bull_call_spread", "Bull Call Spread", "Spread (debit)", "Bullish", "IV percentile 30–85 (short leg offsets IV/theta)", "5–15 days",
                     "Take profit at ~70% of max profit; exit if underlying breaks the setup stop."),
    StrategyTemplate("bear_put_spread", "Bear Put Spread", "Spread (debit)", "Bearish", "IV percentile 30–85", "5–15 days",
                     "Take profit at ~70% of max profit; exit if underlying breaks the setup stop."),
    StrategyTemplate("bull_put_spread", "Bull Put Spread", "Spread (credit)", "Bullish / range with support", "IV percentile ≥ 50 (rich premium to sell)", "3–15 days",
                     "Take profit at 50% of credit; exit if the index closes below the short strike or loss reaches 1.5× credit."),
    StrategyTemplate("bear_call_spread", "Bear Call Spread", "Spread (credit)", "Bearish / range with resistance", "IV percentile ≥ 50", "3–15 days",
                     "Take profit at 50% of credit; exit if the index closes above the short strike or loss reaches 1.5× credit."),
    StrategyTemplate("long_straddle", "Long Straddle", "Volatility (long)", "Large move, direction unknown", "IV percentile ≤ 30 with volatility contraction", "7–30 days",
                     "Exit on a move beyond a breakeven or when half the time value is gone without a move.", "Theta works against the position every day."),
    StrategyTemplate("long_strangle", "Long Strangle", "Volatility (long)", "Large move, direction unknown", "IV percentile ≤ 25 with volatility contraction", "7–30 days",
                     "Exit on a move beyond a breakeven or at a 40% premium loss.", "Needs a bigger move than a straddle."),
    StrategyTemplate("short_straddle", "Short Straddle", "Volatility (short)", "Range-bound", "IV percentile ≥ 70, no trend (ADX < 20), no panic/high-vol regime", "3–10 days",
                     "Take profit at 30–50% of credit; exit if the index breaches a breakeven.", "UNLIMITED RISK. Margin-intensive. Gap risk from events."),
    StrategyTemplate("short_strangle", "Short Strangle", "Volatility (short)", "Range-bound", "IV percentile ≥ 60, no trend, strikes outside OI walls", "3–10 days",
                     "Take profit at 50% of credit; exit if the index closes beyond a short strike.", "UNLIMITED RISK. Margin-intensive. Gap risk from events."),
]}


def build_strategies(chain: pd.DataFrame, spot: float, state: dict, iv_pct: Optional[float], atm_iv_by_expiry: Dict,
                     regime: Optional[dict], oi: Dict[str, List[dict]], underlying_setups: List[dict], lot_size: int,
                     chain_cfg: ChainConfig, hist_closes: Optional[pd.Series], regime_mask: Optional[pd.Series],
                     sessions_to: Dict, high_events_before: Optional[Dict] = None) -> Dict[str, List[dict]]:
    direction = state["direction"]
    reg_name = regime["regime"] if regime else None
    stressed = reg_name == "Panic/Selloff" or state["volatility"] == "High volatility" and (state.get("adx") or 0) >= 25
    bullish_setup = next((u for u in underlying_setups if u["direction"] == "LONG" and u["status"] == "VALID"), None)
    bearish_setup = next((u for u in underlying_setups if u["direction"] == "SHORT" and u["status"] == "VALID"), None)
    ivp = iv_pct
    ivp_txt = "n/a" if ivp is None else f"{ivp:.0f}"
    step = _strike_step(chain)
    near = _expiry(chain, 3, 10)
    mid_exp = _expiry(chain, 5, 15)
    long_exp = _expiry(chain, 7, 30)
    sup = oi.get("support") or []
    res = oi.get("resistance") or []

    def cond(text, ok):
        return {"condition": text, "passed": bool(ok)}

    ev = high_events_before or {}
    no_event = lambda exp: cond("No high-impact economic release before expiry" + (f" (found: {', '.join(ev.get(exp, []))})" if ev.get(exp) else ""),
                                not ev.get(exp))

    iv_known = ivp is not None
    specs = {
        "long_call": ([cond(f"Market state bullish (is {direction})", direction == "Bullish"), cond(f"IV percentile < 70 (is {ivp_txt})", iv_known and ivp < 70),
                       cond("Not in Panic/Selloff", reg_name != "Panic/Selloff"), cond("A VALID bullish underlying setup exists", bullish_setup is not None)], near),
        "long_put": ([cond(f"Market state bearish (is {direction})", direction == "Bearish"), cond(f"IV percentile < 70 (is {ivp_txt})", iv_known and ivp < 70),
                      cond("A VALID bearish underlying setup exists", bearish_setup is not None)], near),
        "bull_call_spread": ([cond(f"Market state bullish (is {direction})", direction == "Bullish"), cond(f"IV percentile 30–85 (is {ivp_txt})", iv_known and 30 <= ivp <= 85),
                              cond("Not in Panic/Selloff", reg_name != "Panic/Selloff"), cond("A VALID bullish underlying setup exists", bullish_setup is not None)], mid_exp),
        "bear_put_spread": ([cond(f"Market state bearish (is {direction})", direction == "Bearish"), cond(f"IV percentile 30–85 (is {ivp_txt})", iv_known and 30 <= ivp <= 85),
                             cond("A VALID bearish underlying setup exists", bearish_setup is not None)], mid_exp),
        "bull_put_spread": ([cond(f"Bullish or range-bound (is {direction})", direction in ("Bullish", "Range-bound")), cond(f"IV percentile ≥ 50 (is {ivp_txt})", iv_known and ivp >= 50),
                             cond("OI support identified below spot", bool(sup)), cond("No stressed volatility regime", not stressed)], near),
        "bear_call_spread": ([cond(f"Bearish or range-bound (is {direction})", direction in ("Bearish", "Range-bound")), cond(f"IV percentile ≥ 50 (is {ivp_txt})", iv_known and ivp >= 50),
                              cond("OI resistance identified above spot", bool(res)), cond("No stressed volatility regime", not stressed)], near),
        "long_straddle": ([cond(f"IV percentile ≤ 30 (is {ivp_txt})", iv_known and ivp <= 30), cond("Volatility contraction (BB squeeze)", state["squeeze"])], long_exp),
        "long_strangle": ([cond(f"IV percentile ≤ 25 (is {ivp_txt})", iv_known and ivp <= 25), cond("Volatility contraction (BB squeeze)", state["squeeze"])], long_exp),
        "short_straddle": ([cond(f"Range-bound (is {direction})", direction == "Range-bound"), cond(f"IV percentile ≥ 70 (is {ivp_txt})", iv_known and ivp >= 70),
                            cond(f"No trend: ADX < 20 (is {state.get('adx')})", (state.get("adx") or 99) < 20), cond("Not Panic/Selloff", reg_name != "Panic/Selloff"),
                            cond("No breakout/breakdown today", not state["events"]), no_event(near)], near),
        "short_strangle": ([cond(f"Range-bound (is {direction})", direction == "Range-bound"), cond(f"IV percentile ≥ 60 (is {ivp_txt})", iv_known and ivp >= 60),
                            cond(f"No trend: ADX < 20 (is {state.get('adx')})", (state.get("adx") or 99) < 20), cond("Not Panic/Selloff", reg_name != "Panic/Selloff"),
                            cond("OI walls on both sides", bool(sup) and bool(res)), cond("No breakout/breakdown today", not state["events"]),
                            no_event(near)], near),
    }

    def construct(key: str, expiry) -> Optional[List[Leg]]:
        if expiry is None:
            return None
        if key == "long_call":
            r = _pick(chain, expiry, "CE", 0.5)
            return [leg_from_row(r, +1)] if r is not None else None
        if key == "long_put":
            r = _pick(chain, expiry, "PE", 0.5)
            return [leg_from_row(r, +1)] if r is not None else None
        if key in ("bull_call_spread", "bear_put_spread"):
            opt, sgn = ("CE", 1) if key == "bull_call_spread" else ("PE", -1)
            buy = _pick(chain, expiry, opt, 0.55)
            if buy is None:
                return None
            u = bullish_setup if sgn == 1 else bearish_setup
            aim = u["targets"][1] if u else buy["strike"] + sgn * 4 * step
            sell = _pick(chain, expiry, opt, strike=aim)
            if sell is None or sgn * (sell["strike"] - buy["strike"]) <= 0:
                sell = _pick(chain, expiry, opt, strike=buy["strike"] + sgn * 4 * step)
            return [leg_from_row(buy, +1), leg_from_row(sell, -1)]
        if key in ("bull_put_spread", "bear_call_spread"):
            opt, sgn = ("PE", -1) if key == "bull_put_spread" else ("CE", 1)
            short = _pick(chain, expiry, opt, 0.30)
            wall = (sup[0]["strike"] if sup else None) if opt == "PE" else (res[0]["strike"] if res else None)
            if wall is not None and short is not None and sgn * (wall - short["strike"]) > 0:
                short = _pick(chain, expiry, opt, strike=wall)  # sell at/beyond the OI wall
            if short is None:
                return None
            hedge = _pick(chain, expiry, opt, strike=short["strike"] + sgn * 2 * step)
            return [leg_from_row(short, -1), leg_from_row(hedge, +1)]
        if key in ("long_straddle", "short_straddle"):
            side = 1 if key == "long_straddle" else -1
            c = _pick(chain, expiry, "CE", strike=spot)
            p = _pick(chain, expiry, "PE", strike=float(c["strike"]) if c is not None else spot)
            return [leg_from_row(c, side), leg_from_row(p, side)] if c is not None and p is not None else None
        if key in ("long_strangle", "short_strangle"):
            side, d = (1, 0.30) if key == "long_strangle" else (-1, 0.16)
            c, p = _pick(chain, expiry, "CE", d), _pick(chain, expiry, "PE", d)
            if key == "short_strangle" and c is not None and p is not None:
                if res and c["strike"] < res[0]["strike"]:
                    c = _pick(chain, expiry, "CE", strike=res[0]["strike"])
                if sup and p["strike"] > sup[0]["strike"]:
                    p = _pick(chain, expiry, "PE", strike=sup[0]["strike"])
            return [leg_from_row(c, side), leg_from_row(p, side)] if c is not None and p is not None else None
        return None

    proposed, not_suitable = [], []
    for key, (conds, expiry) in specs.items():
        t = TEMPLATES[key]
        base = {"key": key, "name": t.name, "category": t.category, "outlook": t.outlook, "iv_condition": t.iv_condition,
                "recommended_expiry": t.expiry_rule, "management": t.management, "risk_note": t.risk_note, "conditions": conds}
        if not all(c["passed"] for c in conds):
            not_suitable.append({**base, "failed": [c["condition"] for c in conds if not c["passed"]]})
            continue
        legs = construct(key, expiry)
        if not legs:
            not_suitable.append({**base, "failed": ["No contracts available for the required expiry window"]})
            continue
        rows = [chain[(chain["expiry"] == pd.Timestamp(lg.expiry).date()) & (chain["strike"] == lg.strike) & (chain["option_type"] == lg.option_type)].iloc[0] for lg in legs]
        illiquid = [f"{int(r['strike'])} {r['option_type']}" for r in rows if not r["liquid"]]
        if illiquid:
            not_suitable.append({**base, "failed": [f"Illiquid leg(s): {', '.join(illiquid)} (spread/OI/volume filters)"]})
            continue
        exp_d = rows[0]["expiry"]
        T = float(rows[0]["T"])
        fwd = float(rows[0]["forward"])
        a = analyze_structure(legs, spot, fwd, T, atm_iv_by_expiry.get(exp_d) or float(np.nanmean([r["iv"] for r in rows])), lot_size,
                              hist_closes, sessions_to.get(exp_d, max(int(rows[0]["dte"] * 5 / 7), 1)), regime_mask)
        cr = capital_and_reward(a, legs, fwd, lot_size)
        proposed.append({**base, "expiry": str(exp_d), "dte": int(rows[0]["dte"]), "lot_size": lot_size,
                         "legs": [lg.to_dict() for lg in legs], **a, **cr, "risk_note": t.risk_note or cr["risk_note"],
                         "iv_at_entry": {"atm_iv_pct": round(100 * (atm_iv_by_expiry.get(exp_d) or 0), 2), "iv_percentile": ivp}})
    return {"proposed": proposed, "not_suitable": not_suitable}
