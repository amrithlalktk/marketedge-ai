"""Option setup finder.

An underlying direction is necessary but NOT sufficient. For each underlying
index setup the selector evaluates every liquid contract on liquidity, spread,
delta, theta burn over the holding period, IV richness vs ATM, days to expiry
and whether the underlying target lies within the IV-implied expected move.

Premium levels are the Black-76 reprice of the contract at the underlying's
stop / T1 / T2 after the expected holding time with IV held constant. The
historical hit rates are those of the UNDERLYING setup (same rules, same
levels); historical option prices are not used, and that is disclosed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from ..validation import Check
from .chain import ChainConfig, expected_move, reprice


@dataclass
class OptionsConfig:
    min_dte: int = 3                  # never enter a 1–3 day hold in an option expiring before the hold ends
    max_dte: int = 21
    delta_min: float = 0.35
    delta_max: float = 0.65
    max_theta_burn_pct: float = 12.0  # theta cost over the holding period as % of premium
    max_stop_loss_pct: float = 50.0   # modelled premium loss at the underlying stop
    min_rr: float = 1.5               # premium reward (T2) : premium risk
    iv_pct_block_long: float = 85.0   # do not buy premium when IV is this rich
    iv_pct_warn_long: float = 70.0
    target_vs_expected_move: float = 1.5  # T1 distance must be ≤ this × 1σ move over the hold
    hold_days_default: float = 2.0    # calendar-ish days used for repricing when history is thin

    def to_dict(self):
        return asdict(self)


def _checks_for(row: pd.Series, entry: float, stop_p: float, t2_p: float, theta_burn: float, iv_pct: Optional[float],
                underlying: dict, em_hold: float, cfg: OptionsConfig, direction: str) -> List[Check]:
    ch: List[Check] = []
    add = lambda n, ok, sev, d: ch.append(Check(n, bool(ok), sev, d))
    add("Contract liquidity", bool(row["liquid"]), "block",
        f"Spread {row['spread_pct']:.1f}% of mid, OI {row['oi_lots']:.0f} lots, volume {row['volume_lots']:.0f} lots")
    add("Days to expiry", row["dte"] >= cfg.min_dte, "block", f"{row['dte']} days to expiry (min {cfg.min_dte})")
    add("Delta", cfg.delta_min <= abs(row["delta"]) <= cfg.delta_max, "block", f"|Δ| = {abs(row['delta']):.2f} (band {cfg.delta_min}–{cfg.delta_max})")
    add("Theta decay", theta_burn <= cfg.max_theta_burn_pct, "block", f"Theta over the holding period ≈ {theta_burn:.1f}% of premium (max {cfg.max_theta_burn_pct:g}%)")
    loss_pct = 100 * (entry - stop_p) / entry
    add("Premium at stop", loss_pct <= cfg.max_stop_loss_pct, "block", f"Modelled premium loss at underlying stop {loss_pct:.0f}% (max {cfg.max_stop_loss_pct:g}%)")
    rr = (t2_p - entry) / max(entry - stop_p, 1e-9)
    add("Premium risk/reward", rr >= cfg.min_rr, "block", f"Premium R:R to T2 = 1:{rr:.2f} (min 1:{cfg.min_rr:g})")
    if iv_pct is None:
        add("IV percentile", False, "warn", "IV history insufficient for a percentile — IV richness unknown")
    else:
        add("IV percentile", iv_pct < cfg.iv_pct_block_long, "block", f"ATM IV percentile {iv_pct:.0f} (buying premium blocked above {cfg.iv_pct_block_long:g})")
        if cfg.iv_pct_warn_long <= iv_pct < cfg.iv_pct_block_long:
            add("IV elevated", False, "warn", f"IV percentile {iv_pct:.0f}: option is expensive; IV contraction can offset a correct direction — consider a debit spread")
    t1_dist = abs(underlying["targets"][0] - underlying["current_price"])
    add("Expected move", t1_dist <= cfg.target_vs_expected_move * em_hold, "block",
        f"Underlying T1 is {t1_dist:.0f} pts away; IV-implied 1σ move over the hold ≈ {em_hold:.0f} pts")
    add("IV model", False, "warn", "Premium levels assume IV stays constant; an IV change will move premiums independently of direction")
    return ch


def contract_score(row: pd.Series, theta_burn: float, rr: float, atm_iv: Optional[float]) -> float:
    liq = 100 * np.clip(1 - row["spread_pct"] / 3.0, 0, 1) * 0.5 + 50 * np.clip(np.log10(max(row["oi_lots"], 1)) / 5, 0, 1)
    delta_fit = 100 * (1 - min(abs(abs(row["delta"]) - 0.5) / 0.2, 1))
    theta_fit = 100 * np.clip(1 - theta_burn / 15, 0, 1)
    rich = 100 if not atm_iv else 100 * np.clip(1 - abs(row["iv"] / atm_iv - 1) / 0.15, 0, 1)
    rr_fit = float(np.interp(rr, [1.0, 1.5, 2.0, 3.0], [10, 50, 75, 100]))
    return round(float(0.3 * liq + 0.2 * delta_fit + 0.2 * theta_fit + 0.1 * rich + 0.2 * rr_fit), 1)


def find_option_setups(chain: pd.DataFrame, spot: float, underlying_setups: List[dict], iv_pct: Optional[float],
                       atm_iv_near: Optional[float], lot_size: int, chain_cfg: ChainConfig, cfg: OptionsConfig,
                       underlying_name: str = "NIFTY", chain_meta: Optional[dict] = None) -> List[dict]:
    out = []
    for u in underlying_setups:
        direction = u["direction"]
        opt_type = "CE" if direction == "LONG" else "PE"
        hold = u.get("expected_holding_days") or cfg.hold_days_default
        hold_cal = float(min(max(hold * 7 / 5, 1.0), 5.0))  # trading → calendar days, 1–5
        pool = chain[(chain["option_type"] == opt_type) & chain["dte"].between(cfg.min_dte, cfg.max_dte) & chain["iv"].notna()]
        pool = pool[(pool["delta"].abs() >= cfg.delta_min - 0.1) & (pool["delta"].abs() <= cfg.delta_max + 0.1)]
        evaluated = []
        for _, row in pool.iterrows():
            entry = float(row["ask"] if row["ask"] > 0 else row["mid"])
            stop_p = reprice(row, u["stop"], 1.0, chain_cfg)
            t1_p = reprice(row, u["targets"][0], hold_cal * 0.6, chain_cfg)
            t2_p = reprice(row, u["targets"][1], hold_cal, chain_cfg)
            theta_burn = 100 * abs(float(row["theta"])) * hold_cal / entry
            rr = (t2_p - entry) / max(entry - stop_p, 1e-9)
            em_hold = expected_move(spot, float(row["iv"]), hold_cal)
            checks = _checks_for(row, entry, stop_p, t2_p, theta_burn, iv_pct, u, em_hold, cfg, direction)
            evaluated.append((row, entry, stop_p, t1_p, t2_p, theta_burn, rr, em_hold, checks, contract_score(row, theta_burn, rr, atm_iv_near)))
        if not evaluated:
            out.append({"underlying_setup": u, "status": "NO_TRADE", "direction": direction,
                        "reason": f"No {opt_type} contract with {cfg.min_dte}–{cfg.max_dte} days to expiry in the delta band"})
            continue
        passing = [e for e in evaluated if not any((not c.passed) and c.severity == "block" for c in e[8])]
        passing_ids = {id(e) for e in passing}
        ranked = sorted(passing or evaluated, key=lambda e: -e[9])
        best = ranked[0]
        row, entry, stop_p, t1_p, t2_p, theta_burn, rr, em_hold, checks, cscore = best
        und_blocks = [c for c in u["checks"] if not c["passed"] and c["severity"] == "block"]
        status = "VALID" if passing and u["status"] == "VALID" else "NO_TRADE"
        score = round(0.6 * u["score"] + 0.4 * cscore, 1)
        exp = row["expiry"]
        label = f"{underlying_name} {int(row['strike'])} {opt_type} {exp.strftime('%d %b %Y')}"
        p = u["probability"]
        out.append({
            "status": status,
            "contract": {"label": label, "underlying": underlying_name, "strike": float(row["strike"]), "option_type": opt_type,
                         "expiry": str(exp), "dte": int(row["dte"]), "lot_size": lot_size, "bid": float(row["bid"]), "ask": float(row["ask"]),
                         "ltp": float(row["ltp"]), "mid": round(float(row["mid"]), 2), "spread_pct": round(float(row["spread_pct"]), 2),
                         "oi": float(row["oi"]), "oi_change": float(row["oi_change"]), "volume": float(row["volume"]),
                         "iv": round(100 * float(row["iv"]), 2), "delta": round(float(row["delta"]), 3), "gamma": round(float(row["gamma"]), 6),
                         "theta": round(float(row["theta"]), 2), "vega": round(float(row["vega"]), 2)},
            "direction": "BULLISH" if direction == "LONG" else "BEARISH",
            "entry": round(entry, 2), "entry_method": "Buy at the ask (conservative fill) at the next session open, only if the underlying entry conditions hold",
            "targets": [round(t1_p, 2), round(t2_p, 2)], "stop": round(max(stop_p, 0.05), 2),
            "level_method": (f"Black-76 reprice at underlying T1 {u['targets'][0]:.0f} (after {hold_cal * 0.6:.1f}d), "
                             f"T2 {u['targets'][1]:.0f} (after {hold_cal:.1f}d) and stop {u['stop']:.0f} (after 1d); IV held constant"),
            "rr": round(rr, 2), "risk_per_lot": round((entry - stop_p) * lot_size, 2), "reward_t2_per_lot": round((t2_p - entry) * lot_size, 2),
            "premium_per_lot": round(entry * lot_size, 2), "theta_burn_pct": round(theta_burn, 1), "expected_move_hold": round(em_hold, 1),
            "score": score, "contract_score": cscore, "underlying_score": u["score"],
            "probability": {**{k: p.get(k) for k in ("t1_hit_rate", "t2_hit_rate", "stop_rate", "neither_rate", "sample_size", "backtest_period",
                                                     "conditioning", "t1_ci95", "t2_ci95", "expectancy_r", "avg_holding_bars", "definitions")},
                            "basis": "underlying",
                            "basis_note": ("Hit rates are for the underlying NIFTY setup reaching its T1/T2 before its stop, using the same rules "
                                           "on index history. Historical option prices were not used; option P&L also depends on IV and time decay.")},
            "expected_holding": "1–3 sessions" if hold <= 3 else f"up to {int(round(hold))} sessions",
            "underlying": {"symbol": u["symbol"], "strategy": u["strategy"], "price": u["current_price"], "entry_zone": u["entry_zone"],
                           "stop": u["stop"], "targets": u["targets"], "rr_t2": u["rr_t2"], "status": u["status"], "score": u["score"],
                           "stop_method": u["stop_method"], "target_methods": u["target_methods"]},
            "checks": [c.to_dict() for c in checks] + [{**c, "name": f"Underlying: {c['name']}"} for c in und_blocks],
            "reasons": u["explanation"]["agreeing"], "risk_factors": u["explanation"]["risk_factors"] + [c.detail for c in checks if not c.passed and c.severity == "warn"],
            "alternatives": [{"label": f"{int(e[0]['strike'])} {opt_type} {e[0]['expiry']}", "entry": round(e[1], 2), "delta": round(float(e[0]['delta']), 2),
                              "rr": round(e[6], 2), "theta_burn_pct": round(e[5], 1), "contract_score": e[9],
                              "passes": not any((not c.passed) and c.severity == "block" for c in e[8])} for e in ranked[1:4]],
            "rejected_contracts": sum(1 for e in evaluated if id(e) not in passing_ids),
            "data": chain_meta or {},
            "as_of": u["as_of"],
        })
    return out
